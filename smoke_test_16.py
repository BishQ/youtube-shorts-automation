"""Smoke test: ONE FULL VIDEO per niche (16 videos total).

Two phases:
  1. PREP (plan + TTS) — light, runs anywhere with Ollama.
       prep_jobs.py <niche> <niche_progress/<niche>> --limit 1

  2. RENDER (images + Wan I2V + final + publish) — heavy, needs the GPU pod.
       Per job: orchestrator.run_images → run_i2v → run_align → run_render → run_publish
       Telegram auto-send fires on publish.

Usage:
    # Local prep only (plan + TTS, no GPU render):
    python smoke_test_16.py --prep

    # Full pipeline on the GPU pod (prep + render + publish):
    python smoke_test_16.py --full

    # Render only — jobs already have plan.json + narration.wav:
    python smoke_test_16.py --render-only

    # Subset of niches:
    python smoke_test_16.py --full --niches history,crime,military
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import traceback
from pathlib import Path

def _default_topics_root() -> Path:
    """Pick the topics folder: bundled (./topics/niches) first, then legacy local path."""
    bundled = Path(__file__).resolve().parent / "topics" / "niches"
    if bundled.is_dir():
        return bundled
    return Path("C:/Users/35383/Documents/topic creator/niche_progress")


DEFAULT_TOPICS_ROOT = _default_topics_root()

ALL_NICHES = [
    "history", "crime", "military", "mythology",
    "science", "psychology", "cosmic", "cults",
    "wealth", "tech_hackers", "lost_tech", "business",
    "edutainment", "sports", "survival", "health",
]


def _prep_niche(niche: str, topics_path: Path, dry_run: bool) -> bool:
    """Plan + TTS for one job in one niche."""
    cmd = [sys.executable, "prep_jobs.py", niche, str(topics_path), "--limit", "1"]
    print(f"\n>>> PREP [{niche}]")
    if dry_run:
        print(f"    (dry-run)  {' '.join(cmd)}")
        return True
    t0 = time.time()
    rc = subprocess.run(cmd, check=False).returncode
    print(f"    {'OK' if rc == 0 else 'FAIL'}  {time.time() - t0:.1f}s")
    return rc == 0


def _ready_jobs_for_render(jobs_root: Path, niches: set[str]) -> list[Path]:
    """Jobs that have plan + narration but no final.mp4 yet."""
    out: list[Path] = []
    for d in sorted(jobs_root.iterdir()):
        if not d.is_dir() or d.name in {"trash", "parallel", "batch_001"}:
            continue
        plan = d / "plan.json"
        narr = d / "narration.wav"
        final = d / "final.mp4"
        if not (plan.is_file() and narr.is_file()):
            continue
        if final.is_file():
            continue
        try:
            niche = json.loads(plan.read_text(encoding="utf-8")).get("niche", "")
        except Exception:
            niche = ""
        if niches and niche not in niches:
            continue
        out.append(d)
    return out


def _render_job(orch, job_dir: Path) -> bool:
    """Run all GPU stages for a single job. Returns True on success."""
    job_id = job_dir.name
    print(f"\n--- RENDER  {job_id}")
    t0 = time.time()
    try:
        orch.run_images(job_id)
        orch.run_i2v(job_id)
        orch.run_align(job_id)
        orch.run_render(job_id)
        orch.run_publish(job_id)
    except Exception as exc:
        print(f"    FAIL ({type(exc).__name__}): {exc}")
        traceback.print_exc()
        return False
    print(f"    OK  {(time.time() - t0) / 60:.1f} min")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="16-niche full-video smoke test")
    ap.add_argument("--prep", action="store_true",
                    help="Plan + TTS only (no GPU render). Default if no mode flag given.")
    ap.add_argument("--render-only", action="store_true",
                    help="Skip prep; render existing job folders that have plan + narration")
    ap.add_argument("--full", action="store_true",
                    help="Prep then render then publish (Telegram). Requires GPU pod.")
    ap.add_argument("--topics-root", type=Path, default=DEFAULT_TOPICS_ROOT)
    ap.add_argument("--niches", default=",".join(ALL_NICHES))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not any([args.prep, args.render_only, args.full]):
        args.prep = True   # safe default

    niches = [n.strip() for n in args.niches.split(",") if n.strip()]
    print(f"Smoke test: {len(niches)} niche(s) | prep={args.prep or args.full} render={args.render_only or args.full}")

    # ── PREP phase ────────────────────────────────────────────────────────────
    if args.prep or args.full:
        if not args.topics_root.is_dir() and not args.dry_run:
            print(f"  ERROR: topics root not found: {args.topics_root}", file=sys.stderr)
            return 1
        ok = fail = 0
        t0 = time.time()
        for niche in niches:
            tp = args.topics_root / niche
            if not tp.is_dir() and not args.dry_run:
                print(f"  SKIP {niche}: no folder at {tp}")
                fail += 1
                continue
            if _prep_niche(niche, tp, args.dry_run):
                ok += 1
            else:
                fail += 1
        print(f"\nPrep done in {(time.time() - t0) / 60:.1f} min  ok={ok} fail={fail}")
        if not args.full:
            return 0 if fail == 0 else 2

    # ── RENDER phase ──────────────────────────────────────────────────────────
    if args.render_only or args.full:
        if args.dry_run:
            print("(dry-run) render phase skipped")
            return 0

        from shorts_pipeline.config.settings import get_settings
        from shorts_pipeline.orchestrator import PipelineOrchestrator

        settings = get_settings()
        orch = PipelineOrchestrator(settings)
        jobs_root = settings.data_dir / "jobs"

        targets = _ready_jobs_for_render(jobs_root, set(niches))
        print(f"\nRender queue: {len(targets)} job(s)")
        for d in targets:
            print(f"  - {d.name}")

        ok = fail = 0
        t0 = time.time()
        for job_dir in targets:
            if _render_job(orch, job_dir):
                ok += 1
            else:
                fail += 1

        print("\n" + "=" * 60)
        print(f"Render done in {(time.time() - t0) / 60:.1f} min")
        print(f"  OK   : {ok}/{len(targets)}")
        print(f"  FAIL : {fail}/{len(targets)}")
        print("\nTelegram should now hold one video per niche under its forum topic.")
        return 0 if fail == 0 else 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
