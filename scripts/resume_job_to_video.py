"""Resume a job from images stage → render final video (no i2v).

Usage:
  python scripts/resume_job_to_video.py marco-polo-dd6e958a
  python scripts/resume_job_to_video.py --latest

Steps run:
  1. delete existing images/ dir (regenerate fresh)
  2. run_images (uses backend per orchestrator routing — Grok for documentary)
  3. run_tts (if narration.wav missing)
  4. run_align (Whisper)
  5. run_render (FFmpeg final.mp4)
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# Load .env
import os
_env = ROOT / ".env"
if _env.is_file():
    for line in _env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v

from shorts_pipeline.config.settings import Settings
from shorts_pipeline.config.ui_store import effective_settings
from shorts_pipeline.context import set_job_id
from shorts_pipeline.jobs.models import JobStatus, PipelineStage
from shorts_pipeline.jobs.store import JobStore
from shorts_pipeline.orchestrator import PipelineOrchestrator
from shorts_pipeline.jobs.paths import resolve_job_dir, legacy_job_dir, niche_job_dir, niche_folder_name


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("job_id", nargs="?", default=None)
    ap.add_argument("--latest", action="store_true", help="Pick the most recent job folder")
    args = ap.parse_args()

    settings = effective_settings(Settings())
    jobs_root = settings.data_dir / "jobs"

    store = JobStore(settings.data_dir / "jobs.sqlite")

    def _all_job_dirs_with_plans() -> list[Path]:
        # Support both layouts:
        #   data/jobs/<job_id>/
        #   data/jobs/<niche>/<job_id>/
        out: list[Path] = []
        # Legacy
        out.extend([p for p in jobs_root.glob("*-*") if (p / "plan.json").is_file()])
        # Niche folders (one level deep)
        for niche_dir in jobs_root.iterdir():
            if not niche_dir.is_dir():
                continue
            out.extend([p for p in niche_dir.glob("*-*") if (p / "plan.json").is_file()])
        return out

    if args.latest or not args.job_id:
        jobs = sorted(_all_job_dirs_with_plans(), key=lambda p: p.stat().st_mtime, reverse=True)
        if not jobs:
            print("No jobs with plan.json found"); return 1
        job_id = jobs[0].name
    else:
        job_id = args.job_id

    # Resolve actual job folder.
    # NOTE: `resolve_job_dir` prefers legacy folders if they exist, but we want
    # to support niche-first layout too, so check both.
    rec = store.get_job(job_id)
    niche = niche_folder_name(rec.topic_type) if rec else "unknown"
    candidates = [
        niche_job_dir(settings, niche, job_id),
        legacy_job_dir(settings, job_id),
        resolve_job_dir(settings, store, job_id),
    ]
    jd = next((p for p in candidates if p.is_dir()), candidates[-1])
    if not jd.is_dir():
        print(f"Job folder not found: {jd}"); return 1
    if not (jd / "plan.json").is_file():
        print(f"plan.json missing in {jd}"); return 1

    print(f"Resuming job: {job_id}")
    print(f"  folder: {jd}")
    plan = json.loads((jd / "plan.json").read_text(encoding="utf-8"))
    print(f"  figure: {plan.get('historical_figure', '?')}")

    # Clean images (folder + sqlite rows — otherwise run_images may skip missing files).
    imgs = jd / "images"
    if imgs.exists():
        shutil.rmtree(imgs)
        print("  cleared images/")
    n_arts = store.delete_all_image_png_artifacts(job_id)
    if n_arts:
        print(f"  cleared {n_arts} image artifact row(s) in jobs.sqlite")

    orch = PipelineOrchestrator(settings, store)
    set_job_id(job_id)

    # Reset job state to start from images
    store.update_job_progress(
        job_id, status=JobStatus.running,
        current_stage=PipelineStage.images,
        last_completed_stage=PipelineStage.plan, clear_error=True,
    )

    t0 = time.time()
    print("\n[1/4] run_images ...")
    orch.run_images(job_id)
    store.update_job_progress(job_id, last_completed_stage=PipelineStage.images,
                              current_stage=PipelineStage.tts)
    print(f"  done in {time.time()-t0:.1f}s")

    narration = jd / "narration.wav"
    if not narration.exists():
        print("\n[2/4] run_tts ...")
        t = time.time()
        orch.run_tts(job_id)
        print(f"  done in {time.time()-t:.1f}s")
    else:
        print(f"\n[2/4] run_tts ... SKIP (narration.wav exists, {narration.stat().st_size//1024} KB)")
    store.update_job_progress(job_id, last_completed_stage=PipelineStage.tts,
                              current_stage=PipelineStage.align)

    print("\n[3/4] run_align ...")
    t = time.time()
    orch.run_align(job_id)
    store.update_job_progress(job_id, last_completed_stage=PipelineStage.align,
                              current_stage=PipelineStage.render)
    print(f"  done in {time.time()-t:.1f}s")

    print("\n[4/4] run_render (skipping i2v) ...")
    t = time.time()
    orch.run_render(job_id)
    store.update_job_progress(job_id,
        status=JobStatus.completed,
        last_completed_stage=PipelineStage.render,
        clear_current_stage=True, clear_error=True,
    )
    print(f"  done in {time.time()-t:.1f}s")

    final = jd / "final.mp4"
    if final.exists():
        print(f"\n✅ DONE: {final}  ({final.stat().st_size//1024//1024} MB, total {(time.time()-t0)/60:.1f} min)")
    else:
        print(f"\n⚠ render finished but {final} not found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
