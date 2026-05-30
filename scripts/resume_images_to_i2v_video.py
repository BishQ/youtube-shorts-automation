"""Images → Wan I2V (per clause) → stitch → final_wan.mp4 (+ final_wan_long.mp4).

Keeps the Ken Burns Short separate:
  - final.mp4 / final_long.mp4     — still images + zoom/pan (unchanged if already present)
  - final_wan.mp4 / final_wan_long.mp4 — Wan animated clips stitched with narration

Pipeline:
  1. TTS + align (if missing)
  2. Optional: ensure final.mp4 exists (Ken Burns) unless --skip-ken-burns
  3. For each clause image → videos/clause_NNN.mp4 via ComfyUI Wan
  4. run_render(visual_mode=wan) → final_wan.mp4 + final_wan_long.mp4

Usage:
  python scripts/resume_images_to_i2v_video.py mike-tyson-33d52a25
  python scripts/resume_images_to_i2v_video.py mike-tyson-33d52a25 --force-wan

Requires ComfyUI :8188 --lowvram, ComfyUI-GGUF + ComfyUI-VideoHelperSuite, Wan GGUF models.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

_env = ROOT / ".env"
if _env.is_file():
    for line in _env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key.startswith("SHORTS_") and key not in os.environ:
            os.environ[key] = val
        elif key and key not in os.environ:
            os.environ[key] = val

os.environ.setdefault("SHORTS_I2V_ENABLED", "true")
os.environ.setdefault("SHORTS_I2V_WORKFLOW_NAME", "wan22_i2v_gguf_q4_local")

from shorts_pipeline.config.settings import Settings  # noqa: E402
from shorts_pipeline.config.ui_store import effective_settings  # noqa: E402
from shorts_pipeline.context import set_job_id  # noqa: E402
from shorts_pipeline.jobs.concurrency import PipelineProcessLock  # noqa: E402
from shorts_pipeline.jobs.models import JobStatus, PipelineStage  # noqa: E402
from shorts_pipeline.jobs.store import JobStore  # noqa: E402
from shorts_pipeline.orchestrator import PipelineOrchestrator  # noqa: E402


def _count_clause_images(images_dir: Path) -> int:
    return len(list(images_dir.glob("clause_*.png")))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("job_id", help="Job folder name under data/jobs/")
    ap.add_argument(
        "--force-wan",
        action="store_true",
        help="Delete videos/ + final_wan*.mp4 before re-running Wan (keeps final.mp4)",
    )
    ap.add_argument(
        "--skip-ken-burns",
        action="store_true",
        help="Do not build final.mp4 (only Wan outputs)",
    )
    ap.add_argument(
        "--ken-burns-only",
        action="store_true",
        help="Only render Ken Burns final.mp4 (no Wan I2V)",
    )
    ap.add_argument("--skip-align", action="store_true", help="Keep existing clause_timings.json")
    args = ap.parse_args()

    settings = effective_settings(Settings())
    settings.i2v_enabled = not args.ken_burns_only
    if settings.i2v_enabled:
        settings.i2v_workflow_name = os.environ.get(
            "SHORTS_I2V_WORKFLOW_NAME", "wan22_i2v_gguf_q4_local"
        )

    jd = settings.data_dir / "jobs" / args.job_id
    if not jd.is_dir():
        print(f"Job not found: {jd}", file=sys.stderr)
        return 1
    if not (jd / "plan.json").is_file():
        print(f"Missing plan.json in {jd}", file=sys.stderr)
        return 1

    images_dir = jd / "images"
    n_img = _count_clause_images(images_dir) if images_dir.is_dir() else 0
    plan = json.loads((jd / "plan.json").read_text(encoding="utf-8"))
    n_clauses = len(plan.get("clauses") or [])
    if n_img < n_clauses:
        print(
            f"Need {n_clauses} images, found {n_img} in {images_dir}.",
            file=sys.stderr,
        )
        return 1

    print(f"Job: {args.job_id}")
    print(f"  figure: {plan.get('historical_figure', '?')}")
    print(f"  images: {n_img}/{n_clauses}")
    print("  outputs:")
    print("    final.mp4 + final_long.mp4       (Ken Burns — stills)")
    print("    final_wan.mp4 + final_wan_long.mp4 (Wan I2V clips)")

    if args.force_wan:
        videos = jd / "videos"
        if videos.is_dir():
            shutil.rmtree(videos)
            print("  cleared videos/")
        for name in ("final_wan.mp4", "final_wan_long.mp4"):
            p = jd / name
            if p.is_file():
                p.unlink()
                print(f"  removed {name}")

    store = JobStore(settings.data_dir / "jobs.sqlite")
    orch = PipelineOrchestrator(settings, store)
    set_job_id(args.job_id)
    t0 = time.time()

    store.update_job_progress(
        args.job_id,
        status=JobStatus.running,
        current_stage=PipelineStage.align,
        last_completed_stage=PipelineStage.images,
        clear_error=True,
    )

    narration = jd / "narration.wav"
    if not narration.is_file():
        print("\n[tts] generating narration.wav ...")
        orch.run_tts(args.job_id)
    else:
        print(f"\n[tts] skip ({narration.stat().st_size // 1024} KB)")

    if args.skip_align and (jd / "clause_timings.json").is_file():
        print("[align] skip")
    else:
        print("[align] running ...")
        orch.run_align(args.job_id)

    ken_final = jd / "final.mp4"
    if not args.skip_ken_burns and (args.ken_burns_only or not ken_final.is_file()):
        print("\n[render] Ken Burns → final.mp4 (+ final_long.mp4) ...")
        orch.run_render(args.job_id, visual_mode="ken_burns")
    elif ken_final.is_file():
        print(f"\n[render] Ken Burns skip — {ken_final.name} exists")
    else:
        print("\n[render] Ken Burns skip (--skip-ken-burns)")

    if args.ken_burns_only:
        print(f"\nDONE (Ken Burns only) in {(time.time() - t0) / 60:.1f} min")
        return 0

    wf = settings.workflows_dir / f"{settings.i2v_workflow_name}.json"
    if not wf.is_file():
        print(f"Missing workflow: {wf}", file=sys.stderr)
        return 1

    print(f"\n[i2v] {n_clauses} clips (one image → one MP4) — expect ~1–2 h on 8GB")
    t = time.time()
    orch.run_i2v(args.job_id)
    print(f"  i2v done in {(time.time() - t) / 60:.1f} min")

    print("\n[render] Wan stitch → final_wan.mp4 (+ final_wan_long.mp4) ...")
    t = time.time()
    out = orch.run_render(
        args.job_id,
        visual_mode="wan",
        out_mp4=jd / "final_wan.mp4",
    )
    print(f"  wan render done in {time.time() - t:.1f}s → {out.name}")

    store.update_job_progress(
        args.job_id,
        status=JobStatus.completed,
        last_completed_stage=PipelineStage.render,
        clear_current_stage=True,
        clear_error=True,
    )

    wan_long = jd / "final_wan_long.mp4"
    print(f"\nDONE in {(time.time() - t0) / 60:.1f} min")
    if ken_final.is_file():
        print(f"  Ken Burns: {ken_final}")
    print(f"  Wan Short: {out}")
    if wan_long.is_file():
        print(f"  Wan Long:  {wan_long}")
    return 0 if out.is_file() else 1


if __name__ == "__main__":
    with PipelineProcessLock(Settings().data_dir):
        raise SystemExit(main())
