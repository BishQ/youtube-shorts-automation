"""Bulk documentary plans — Gemma + MAIN arch, plan.json only (11 clauses).

Creates pipeline jobs under data/jobs/<figure-slug>-<id>/plan.json using the same
MAIN cinematic planner as gemma_volume_bench (NOT the Web UI JSON planner).
Plans validate against schema.CLAUSE_COUNT (11).

Topics: topics/famous_people_1000/batch_*/topics.txt (~1000 figures).
Skips any figure that already has a non-empty plan.json in a matching job folder.

Requirements:
  - LM Studio on :1234 with gemma-4-e4b (or set SHORTS_LOCAL_LLM_MODEL)
  - .env loaded automatically

Examples:
  python scripts/bulk_documentary_plans_gemma.py --dry-run
  python scripts/bulk_documentary_plans_gemma.py --limit 5
  python scripts/bulk_documentary_plans_gemma.py
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

_env = ROOT / ".env"
if _env.is_file():
    for line in _env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val

import bench_lmstudio_v2 as bv2  # noqa: E402
from shorts_pipeline.config.settings import Settings  # noqa: E402
from shorts_pipeline.jobs.models import (  # noqa: E402
    ArtifactType,
    JobConfigSnapshot,
    JobStatus,
    PipelineStage,
)
from shorts_pipeline.jobs.pipeline_runner import PipelineRunner  # noqa: E402
from shorts_pipeline.jobs.store import JobStore  # noqa: E402
from shorts_pipeline.planner.cinematic import ARCHITECTURES, with_retries  # noqa: E402
from shorts_pipeline.planner.niche_caps import caps_for, count_syllables  # noqa: E402

NICHE = "documentary"
ARCH = ARCHITECTURES["MAIN"]
LINE_NUM_PREFIX = re.compile(r"^\s*\d+[\.\)]\s*")
FIGURE_SLUG_RE = re.compile(r"[^a-z0-9]+")
DEFAULT_TOPICS_ROOT = ROOT / "topics" / "famous_people_1000"
DEFAULT_MODEL = "gemma-4-e4b"
CTX = 12288
TIMEOUT_S = 900.0
MAX_RETRIES = 3
PROGRESS_DIR = ROOT / "scripts_out" / "documentary_gemma_plans"


def _figure_slug(name: str) -> str:
    return FIGURE_SLUG_RE.sub("-", name.lower()).strip("-")[:32]


def _ensure_dummy_bgm(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.stat().st_size > 44:
        return path
    with wave.open(str(path), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(22050)
        w.writeframes(b"\x00\x00" * 22050)
    return path


def load_famous_people(topics_root: Path) -> list[tuple[int, str]]:
    """All figures from batch_*/topics.txt, deduped by normalized name, stable order."""
    seen: set[str] = set()
    out: list[tuple[int, str]] = []
    counter = 0
    for topics_file in sorted(topics_root.glob("batch_*/topics.txt")):
        for raw in topics_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line:
                continue
            name = LINE_NUM_PREFIX.sub("", line).strip()
            if not name:
                continue
            key = name.casefold()
            if key in seen:
                continue
            seen.add(key)
            counter += 1
            out.append((counter, name))
    return out


def existing_job_with_plan(settings: Settings, figure: str) -> Path | None:
    """Return job dir if this figure already has plan.json on disk."""
    slug = _figure_slug(figure)
    jobs_dir = settings.data_dir / "jobs"
    if not jobs_dir.is_dir():
        return None
    best: Path | None = None
    for d in jobs_dir.iterdir():
        if not d.is_dir() or not d.name.startswith(f"{slug}-"):
            continue
        plan = d / "plan.json"
        if plan.is_file() and plan.stat().st_size > 200:
            if best is None or d.stat().st_mtime > best.stat().st_mtime:
                best = d
    return best


def _append_progress(row: dict) -> None:
    PROGRESS_DIR.mkdir(parents=True, exist_ok=True)
    path = PROGRESS_DIR / "_progress.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--topics-root",
        type=Path,
        default=DEFAULT_TOPICS_ROOT,
        help="famous_people_1000 folder (default: topics/famous_people_1000)",
    )
    ap.add_argument("--limit", type=int, default=0, help="max new plans (0 = all)")
    ap.add_argument("--start", type=int, default=1, help="skip index < START (1-based)")
    ap.add_argument("--dry-run", action="store_true", help="list only, no LLM")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="LM Studio model id")
    ap.add_argument(
        "--no-unload",
        action="store_true",
        help="keep model loaded in VRAM when finished",
    )
    ap.add_argument(
        "--run-pipeline",
        action="store_true",
        help="after each plan, run full pipeline (images→tts→align→i2v→render)",
    )
    args = ap.parse_args()

    if not args.topics_root.is_dir():
        print(f"topics root not found: {args.topics_root}", file=sys.stderr)
        return 1

    figures = load_famous_people(args.topics_root)
    if not figures:
        print("no topics loaded", file=sys.stderr)
        return 1

    settings = Settings()
    settings.local_llm_model = args.model
    settings.local_llm_timeout_s = TIMEOUT_S
    store = JobStore(settings.data_dir / "jobs.sqlite")
    bgm = _ensure_dummy_bgm(settings.data_dir / "_bulk_plan_bgm.wav")

    todo: list[tuple[int, str]] = []
    skipped = 0
    for idx, name in figures:
        if idx < args.start:
            continue
        if existing_job_with_plan(settings, name):
            skipped += 1
            continue
        todo.append((idx, name))

    print(
        f"documentary MAIN+Gemma | topics={len(figures)} | already_done={skipped} | "
        f"to_generate={len(todo)} | model={args.model} | LM={settings.local_llm_base_url}",
        flush=True,
    )

    if args.limit:
        todo = todo[: args.limit]

    if args.dry_run:
        for idx, name in todo[:20]:
            print(f"  [{idx:04d}] would create: {name}")
        if len(todo) > 20:
            print(f"  ... and {len(todo) - 20} more")
        return 0

    if not todo:
        print("nothing to do — all figures have plan.json", flush=True)
        return 0

    print("Loading model...", flush=True)
    bv2._load_model(settings.local_llm_base_url, args.model, CTX)

    ok = fail = 0
    t_batch = time.time()
    min_w, max_w, _ = caps_for(NICHE)
    pipeline_runner: PipelineRunner | None = None
    if args.run_pipeline:
        pipeline_runner = PipelineRunner(settings, store)

    try:
        for idx, figure in todo:
            print(f"\n[{idx:04d}/{len(figures)}] {figure}", flush=True)
            t0 = time.time()
            row: dict = {
                "idx": idx,
                "figure": figure,
                "niche": NICHE,
                "model": args.model,
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            try:
                plan_dict, dbg = with_retries(
                    ARCH,
                    settings=settings,
                    niche=NICHE,
                    topic=figure,
                    max_retries=MAX_RETRIES,
                    timeout_s=TIMEOUT_S,
                )
                plan_dict["niche"] = NICHE
                plan_dict["video_mode"] = "normal"
                plan_dict["source_index"] = idx
                plan_dict["source_topics_root"] = str(args.topics_root)

                job_id = store.create_job(
                    JobConfigSnapshot(
                        figure_name=figure,
                        topic_type=NICHE,
                        language="en",
                        bgm_path=str(bgm),
                        watermark_enabled=False,
                        end_plate_enabled=True,
                        overlay_enabled=False,
                    )
                )
                job_dir = settings.data_dir / "jobs" / job_id
                job_dir.mkdir(parents=True, exist_ok=True)
                plan_path = job_dir / "plan.json"
                plan_path.write_text(
                    json.dumps(plan_dict, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                store.add_artifact(
                    job_id,
                    PipelineStage.plan,
                    ArtifactType.plan_json,
                    plan_path,
                    meta={"figure": figure, "planner": "MAIN", "model": args.model},
                )
                store.update_job_progress(
                    job_id,
                    status=JobStatus.paused,
                    clear_current_stage=True,
                    last_completed_stage=PipelineStage.plan,
                    clear_error=True,
                )

                words = len(plan_dict.get("full_script", "").split())
                elapsed = round(time.time() - t0, 1)
                row.update(
                    success=True,
                    job_id=job_id,
                    plan_path=str(plan_path),
                    words=words,
                    in_band=min_w <= words <= max_w,
                    syllables=count_syllables(plan_dict.get("full_script", "")),
                    attempts=dbg.get("succeeded_on", 1),
                    seconds=elapsed,
                )
                ok += 1
                print(
                    f"  OK job={job_id}  {elapsed}s  words={words}  "
                    f"in_band={row['in_band']}  attempt={row['attempts']}",
                    flush=True,
                )
                if pipeline_runner is not None:
                    print(f"  [pipeline] starting full run for {job_id} ...", flush=True)
                    t_pipe = time.time()
                    try:
                        pipeline_runner.run_job(job_id)
                        rec = store.get_job(job_id)
                        row["pipeline_status"] = rec.status.value if rec else "unknown"
                        row["pipeline_seconds"] = round(time.time() - t_pipe, 1)
                        print(
                            f"  [pipeline] done status={row['pipeline_status']} "
                            f"in {row['pipeline_seconds']}s",
                            flush=True,
                        )
                    except Exception as pipe_exc:
                        row["pipeline_status"] = "failed"
                        row["pipeline_error"] = str(pipe_exc)[:400]
                        print(f"  [pipeline] FAIL: {row['pipeline_error'][:160]}", flush=True)
            except Exception as exc:
                elapsed = round(time.time() - t0, 1)
                row.update(success=False, seconds=elapsed, error=str(exc)[:500])
                fail += 1
                print(f"  FAIL {elapsed}s  {row['error'][:160]}", flush=True)
            _append_progress(row)
    finally:
        if not args.no_unload:
            bv2._unload_all(settings.local_llm_base_url)

    print(
        f"\nDone in {(time.time() - t_batch) / 60:.1f} min — ok={ok} fail={fail} "
        f"(skipped_existing={skipped})",
        flush=True,
    )
    print(f"Progress log: {PROGRESS_DIR / '_progress.jsonl'}", flush=True)
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
