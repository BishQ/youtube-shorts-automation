"""Smoke test: ONE FULL VIDEO per niche (16 videos total).

PREP phase (light, can run anywhere with vLLM):
    prep_jobs.py <niche> <topics/niches/<niche>> --limit 1 --script-only
    → writes data/jobs/<slug>-<id>/plan.json

RENDER phase (heavy, needs GPU pod with ComfyUI + Kokoro):
    For each job folder:
      1. Register job in SQLite (build_job_config_for_disk_import + import_job_if_missing)
      2. Register plan.json artifact
      3. orch.run_tts       — Kokoro narration.wav
      4. orch.run_align     — Whisper word alignment → ranges.json
      5. orch.run_images    — Qwen Image 2512 (one PNG per clause)
      6. orch.run_i2v       — Wan 2.2 I2V (one MP4 per clause)
      7. orch.run_render    — FFmpeg final.mp4
      8. orch.run_publish   — publish_package.json + Telegram auto-send

Usage:
    python smoke_test_16.py --prep              # plans only (no GPU)
    python smoke_test_16.py --full              # prep + render + publish (GPU pod)
    python smoke_test_16.py --render-only       # render existing job folders
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
    """Plan only (no TTS — render phase regenerates TTS via Kokoro)."""
    cmd = [sys.executable, "prep_jobs.py", niche, str(topics_path),
           "--limit", "1", "--script-only"]
    print(f"\n>>> PREP [{niche}]")
    if dry_run:
        print(f"    (dry-run)  {' '.join(cmd)}")
        return True
    t0 = time.time()
    rc = subprocess.run(cmd, check=False).returncode
    print(f"    {'OK' if rc == 0 else 'FAIL'}  {time.time() - t0:.1f}s")
    return rc == 0


def _ready_jobs_for_render(jobs_root: Path, niches: set[str]) -> list[Path]:
    """Jobs that have plan.json but no final.mp4 yet, filtered by niche."""
    out: list[Path] = []
    for d in sorted(jobs_root.iterdir()):
        if not d.is_dir() or d.name in {"trash", "parallel", "batch_001"}:
            continue
        plan = d / "plan.json"
        final = d / "final.mp4"
        if not plan.is_file() or final.is_file():
            continue
        try:
            niche = (json.loads(plan.read_text(encoding="utf-8")).get("niche") or "").strip().lower()
        except Exception:
            niche = ""
        if niches and niche not in niches:
            continue
        out.append(d)
    return out


def _register_job_in_db(store, settings, job_dir: Path) -> str:
    """Import job from disk into SQLite if missing; register plan artifact."""
    from shorts_pipeline.jobs.image_recovery import (
        build_job_config_for_disk_import,
        default_bgm_path_for_reconcile,
        ensure_plan_artifact_from_disk,
        resolve_folder_to_job_id,
    )
    jid = resolve_folder_to_job_id(settings, str(job_dir))
    if store.get_job(jid) is None:
        bgm = default_bgm_path_for_reconcile(settings)
        if not bgm:
            raise RuntimeError(
                "No BGM file found. Place one at 'extra tools/bgm.mp3' or set SHORTS_DEFAULT_BGM_PATH."
            )
        cfg = build_job_config_for_disk_import(settings, jid, bgm)
        store.import_job_if_missing(jid, cfg)
        print(f"    registered job in DB  bgm={Path(bgm).name}")
    ensure_plan_artifact_from_disk(store, settings, jid)
    return jid


def _render_job(orch, store, settings, job_dir: Path) -> bool:
    """Register + run all pipeline stages for one job."""
    print(f"\n--- RENDER  {job_dir.name}")
    t0 = time.time()
    try:
        jid = _register_job_in_db(store, settings, job_dir)
        print(f"    run_tts…");     orch.run_tts(jid)
        print(f"    run_align…");   orch.run_align(jid)
        print(f"    run_images…");  orch.run_images(jid)
        print(f"    run_i2v…");     orch.run_i2v(jid)
        print(f"    run_render…");  orch.run_render(jid)
        print(f"    run_publish…"); orch.run_publish(jid)
    except Exception as exc:
        print(f"    FAIL ({type(exc).__name__}): {exc}")
        traceback.print_exc()
        return False
    print(f"    OK  {(time.time() - t0) / 60:.1f} min")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="16-niche full-video smoke test")
    ap.add_argument("--prep", action="store_true",
                    help="Plans only (no GPU). Default if no mode flag given.")
    ap.add_argument("--render-only", action="store_true",
                    help="Skip prep; render existing job folders")
    ap.add_argument("--full", action="store_true",
                    help="Prep then render then publish (Telegram). Requires GPU pod.")
    ap.add_argument("--topics-root", type=Path, default=DEFAULT_TOPICS_ROOT)
    ap.add_argument("--niches", default=",".join(ALL_NICHES))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not any([args.prep, args.render_only, args.full]):
        args.prep = True

    niches = [n.strip() for n in args.niches.split(",") if n.strip()]
    print(f"Smoke test: {len(niches)} niche(s) | prep={args.prep or args.full} render={args.render_only or args.full}")

    # ── PREP ──────────────────────────────────────────────────────────────────
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

    # ── RENDER ────────────────────────────────────────────────────────────────
    if args.render_only or args.full:
        if args.dry_run:
            print("(dry-run) render phase skipped")
            return 0

        from shorts_pipeline.config.settings import get_settings
        from shorts_pipeline.jobs.store import JobStore
        from shorts_pipeline.orchestrator import PipelineOrchestrator

        settings = get_settings()
        store = JobStore(settings.data_dir / "jobs.sqlite")
        orch = PipelineOrchestrator(settings, store)
        jobs_root = settings.data_dir / "jobs"

        targets = _ready_jobs_for_render(jobs_root, set(niches))
        print(f"\nRender queue: {len(targets)} job(s)")
        for d in targets:
            print(f"  - {d.name}")

        ok = fail = 0
        t0 = time.time()
        for job_dir in targets:
            if _render_job(orch, store, settings, job_dir):
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
