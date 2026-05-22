"""Parallel batch runner for the niche → plan → TTS → fit_narration pipeline.

Architecture
------------
Two phases, each with a different concurrency profile:

  Phase 1 — PLAN GENERATION    (network-bound, LLM API calls)
    • ThreadPoolExecutor with ``plan_workers`` workers (default 8).
    • Each worker calls ``generate_plan_for_topic`` (Gemini → DeepSeek fallback).
    • Topics that succeed land in a queue; failures land in ``failures.csv``.

  Phase 2 — TTS + fit_narration  (GPU/CPU-bound, Kokoro local model)
    • ThreadPoolExecutor with ``tts_workers`` workers (default 2).
    • Too many parallel Kokoro calls contend on the same model weights and
      slow down. 2 workers is the sweet spot on a single-GPU machine.
    • Each worker renders narration.wav, then runs fit_narration in-process
      to land the audio in the 55-59.5 s band.

Restartable
-----------
Jobs whose ``plan.json`` + ``narration.wav`` already exist AND have a row in
``calibration_results.csv`` are skipped. Kill the script any time; restart
picks up where it stopped.

Usage
-----
    # Run a batch of topics from a niche-specific JSON file
    python parallel_runner.py history topics_history.json --plan-workers 10 --tts-workers 2

    # Topics JSON format:
    #   [{"topic": "John Napier"}, {"topic": "Hatshepsut"}, ...]

    # Or use the existing batch folder convention from prep_jobs / make_scripts
    python parallel_runner.py history "C:/topic-creator/output/famous_people_1000/batch_001"
"""

from __future__ import annotations

import argparse
import csv
import json
import queue
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from shorts_pipeline.config.settings import Settings  # noqa: E402
from shorts_pipeline.tts_worker.kokoro import KokoroTTSClient  # noqa: E402

import importlib
_PREP = importlib.import_module("prep_jobs")
_FIT = importlib.import_module("fit_narration")


# ── Output paths ───────────────────────────────────────────────────────────
FAILURES_CSV = ROOT / "failures.csv"
PROGRESS_CSV = ROOT / "parallel_progress.csv"

# Single shared lock for failures.csv / progress writes.
_io_lock = threading.Lock()


def _slug(topic: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")[:60] or "topic"


def _job_dir(jobs_root: Path, idx: int, topic: str) -> Path:
    return jobs_root / f"{idx:03d}_{_slug(topic)}"


def _record_failure(topic: str, stage: str, err: str) -> None:
    with _io_lock:
        write_header = not FAILURES_CSV.exists()
        with FAILURES_CSV.open("a", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            if write_header:
                w.writerow(["timestamp", "topic", "stage", "error"])
            w.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), topic, stage, err[:500]])


def _record_progress(topic: str, status: str, plan_words: int, duration: float) -> None:
    with _io_lock:
        write_header = not PROGRESS_CSV.exists()
        with PROGRESS_CSV.open("a", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            if write_header:
                w.writerow(["timestamp", "topic", "status", "plan_words", "duration_s"])
            w.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), topic, status,
                       plan_words, f"{duration:.2f}"])


# ── Phase 1: plan generation ───────────────────────────────────────────────

def generate_plan_for_job(
    settings: Settings,
    niche_sys: str,
    niche_user_fn,
    niche: str,
    topic: str,
    job_dir: Path,
) -> dict | None:
    plan_path = job_dir / "plan.json"
    if plan_path.is_file():
        try:
            return json.loads(plan_path.read_text(encoding="utf-8"))
        except Exception:
            plan_path.unlink()  # corrupt, regenerate

    try:
        plan = _PREP.generate_plan_for_topic(settings, niche_sys, niche_user_fn,
                                             topic, niche=niche)
        job_dir.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        return plan
    except Exception as e:
        _record_failure(topic, "plan", str(e))
        return None


# ── Phase 2: TTS + fit ─────────────────────────────────────────────────────

# Kokoro client is process-wide singleton — building KPipeline is expensive,
# so we cache it. Lock guards the model from concurrent forward passes.
_tts_lock = threading.Lock()


def render_and_fit(
    settings: Settings,
    plan: dict,
    job_dir: Path,
    topic: str,
    *,
    target_min: float,
    target_max: float,
    hard_cap: float,
    max_speed: float,
    min_speed: float,
) -> tuple[bool, float]:
    wav = job_dir / "narration.wav"
    text = (plan.get("full_script") or "").strip()
    if not text:
        _record_failure(topic, "tts", "plan has no full_script")
        return False, 0.0

    try:
        if not _PREP._wav_ok(wav):
            with _tts_lock:
                KokoroTTSClient(settings).synthesize_wav(text, wav)
        # Run fit_narration in-process
        with _tts_lock:
            _FIT.fit_one(
                job_dir,
                target_min=target_min,
                target_max=target_max,
                hard_cap=hard_cap,
                max_speed=max_speed,
                min_speed=min_speed,
                dry_run=False,
            )
        dur = _FIT._probe_duration(wav)
        return True, dur
    except Exception as e:
        _record_failure(topic, "tts/fit", str(e))
        return False, 0.0


# ── Topic loaders ──────────────────────────────────────────────────────────

def load_topics_from_path(path: Path) -> list[tuple[int, str]]:
    if path.is_file() and path.suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        return [(i + 1, item["topic"] if isinstance(item, dict) else str(item))
                for i, item in enumerate(data)]
    # Fall back to prep_jobs loader (supports batch dirs and topics.txt files)
    return _PREP.load_topics(path)


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("niche", help="One of: history, science, mythology, ...")
    ap.add_argument("topics_path", help="JSON file, topics.txt, or batch folder")
    ap.add_argument("--jobs-root", default="data/jobs/parallel",
                    help="Where job folders are written")
    ap.add_argument("--plan-workers", type=int, default=8,
                    help="Parallel LLM workers (network-bound)")
    ap.add_argument("--tts-workers", type=int, default=2,
                    help="Parallel TTS workers (GPU contention bound)")
    ap.add_argument("--limit", type=int, default=0,
                    help="Stop after N topics (0 = all)")
    ap.add_argument("--target-min", type=float, default=55.0)
    ap.add_argument("--target-max", type=float, default=59.5)
    ap.add_argument("--hard-cap", type=float, default=59.5)
    ap.add_argument("--max-speed", type=float, default=1.22)
    ap.add_argument("--min-speed", type=float, default=0.85)
    args = ap.parse_args()

    settings = Settings()
    niche_sys, niche_user_fn = _PREP.load_niche(args.niche)
    jobs_root = Path(args.jobs_root) / args.niche
    jobs_root.mkdir(parents=True, exist_ok=True)

    topics = load_topics_from_path(Path(args.topics_path))
    if args.limit > 0:
        topics = topics[:args.limit]
    if not topics:
        sys.exit("no topics found")

    print(f"niche={args.niche}  topics={len(topics)}  jobs_root={jobs_root}")
    print(f"plan_workers={args.plan_workers}  tts_workers={args.tts_workers}")
    print(f"target band [{args.target_min}, {args.target_max}]s, hard cap {args.hard_cap}s")

    # ─── Phase 1: plans in parallel ───
    t0 = time.time()
    ready_for_tts: queue.Queue[tuple[int, str, Path, dict]] = queue.Queue()

    def _plan_task(idx: int, topic: str):
        job_dir = _job_dir(jobs_root, idx, topic)
        plan = generate_plan_for_job(settings, niche_sys, niche_user_fn,
                                     args.niche, topic, job_dir)
        if plan is not None:
            ready_for_tts.put((idx, topic, job_dir, plan))
        return idx, topic, plan is not None

    plan_ok = plan_fail = 0
    with ThreadPoolExecutor(max_workers=args.plan_workers) as pool:
        futures = [pool.submit(_plan_task, idx, topic) for idx, topic in topics]
        for f in as_completed(futures):
            idx, topic, ok = f.result()
            if ok:
                plan_ok += 1
                print(f"  [plan {plan_ok+plan_fail:>4}/{len(topics)}] OK   {topic[:60]}")
            else:
                plan_fail += 1
                print(f"  [plan {plan_ok+plan_fail:>4}/{len(topics)}] FAIL {topic[:60]}")

    t_plans = time.time() - t0
    print(f"\nPhase 1 done: {plan_ok} ok, {plan_fail} fail in {t_plans/60:.1f} min\n")

    # ─── Phase 2: TTS + fit, lower parallelism ───
    t1 = time.time()
    tts_ok = tts_fail = 0
    pending = []
    while not ready_for_tts.empty():
        pending.append(ready_for_tts.get())

    def _tts_task(idx: int, topic: str, job_dir: Path, plan: dict):
        ok, dur = render_and_fit(
            settings, plan, job_dir, topic,
            target_min=args.target_min,
            target_max=args.target_max,
            hard_cap=args.hard_cap,
            max_speed=args.max_speed,
            min_speed=args.min_speed,
        )
        status = "ok" if ok and args.target_min <= dur <= args.target_max else (
                 "out-of-band" if ok else "fail")
        wc = len((plan.get("full_script") or "").split())
        _record_progress(topic, status, wc, dur)
        return idx, topic, ok, dur

    with ThreadPoolExecutor(max_workers=args.tts_workers) as pool:
        futures = [pool.submit(_tts_task, *p) for p in pending]
        for f in as_completed(futures):
            idx, topic, ok, dur = f.result()
            if ok:
                tts_ok += 1
                in_band = args.target_min <= dur <= args.target_max
                tag = "OK" if in_band else "OUT"
                print(f"  [tts  {tts_ok+tts_fail:>4}/{len(pending)}] {tag:>3}  {dur:5.1f}s  {topic[:55]}")
            else:
                tts_fail += 1
                print(f"  [tts  {tts_ok+tts_fail:>4}/{len(pending)}] FAIL       {topic[:55]}")

    t_tts = time.time() - t1
    total = time.time() - t0

    print()
    print("=" * 60)
    print(f"Final: {plan_ok}/{len(topics)} plans, {tts_ok}/{len(pending)} renders")
    print(f"Phase 1 (plans): {t_plans/60:.1f} min  ({plan_ok/t_plans*60:.1f} plans/min)")
    print(f"Phase 2 (tts) : {t_tts/60:.1f} min  ({tts_ok/t_tts*60:.1f} renders/min)")
    print(f"Total: {total/60:.1f} min for {len(topics)} topics")
    print(f"Failures recorded in: {FAILURES_CSV}")
    print(f"Progress  recorded in: {PROGRESS_CSV}")


if __name__ == "__main__":
    main()
