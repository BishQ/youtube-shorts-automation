"""Run ComfyUI image + Wan I2V stages using an existing plan (script)."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

_env = _ROOT / ".env"
if _env.is_file():
    for line in _env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key.startswith("SHORTS_"):
            os.environ[key] = val


def _promote_failed_plan(job_dir: Path) -> Path:
    failed = job_dir / "plan.failed.json"
    if not failed.is_file():
        raise FileNotFoundError(f"No plan.json or plan.failed.json in {job_dir}")
    plan = job_dir / "plan.json"
    shutil.copy2(failed, plan)
    return plan


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-id", required=True, help="Existing job id (e.g. genghis-khan-0f983561)")
    parser.add_argument(
        "--promote-failed",
        action="store_true",
        help="Copy plan.failed.json → plan.json before running",
    )
    parser.add_argument(
        "--dev-skip-plan-validation",
        action="store_true",
        help="Load plan without word/syllable validation (temporary testing)",
    )
    parser.add_argument("--images", action="store_true", help="Run image generation stage")
    parser.add_argument("--i2v", action="store_true", help="Run image-to-video stage")
    parser.add_argument(
        "--max-clauses",
        type=int,
        default=None,
        help="Only use first N clauses (quick GPU test on 8GB)",
    )
    args = parser.parse_args()

    if not args.images and not args.i2v:
        args.images = True
        args.i2v = True

    if args.dev_skip_plan_validation:
        os.environ["SHORTS_DEV_SKIP_PLAN_VALIDATION"] = "1"

    from shorts_pipeline.config.settings import Settings
    from shorts_pipeline.jobs.models import ArtifactType, PipelineStage
    from shorts_pipeline.jobs.paths import resolve_job_dir
    from shorts_pipeline.jobs.store import JobStore
    from shorts_pipeline.orchestrator import PipelineOrchestrator

    settings = Settings()
    store = JobStore(settings.data_dir / "jobs.sqlite")
    job_id = args.job_id

    if store.get_job(job_id) is None:
        print(f"ERROR: job {job_id} not in database. Create it in Web UI first.", file=sys.stderr)
        return 1

    job_dir = resolve_job_dir(settings, store, job_id)
    plan_path = job_dir / "plan.json"
    if args.promote_failed or not plan_path.is_file():
        plan_path = _promote_failed_plan(job_dir)
        print(f"Promoted plan: {plan_path}")

    if not plan_path.is_file():
        print(f"ERROR: missing {plan_path}", file=sys.stderr)
        return 1

    if args.max_clauses is not None and args.max_clauses > 0:
        data = json.loads(plan_path.read_text(encoding="utf-8"))
        clauses = data.get("clauses")
        if isinstance(clauses, list) and len(clauses) > args.max_clauses:
            data["clauses"] = clauses[: args.max_clauses]
            data["full_script"] = " ".join(
                c.get("text", "").strip() for c in data["clauses"] if isinstance(c, dict)
            ).strip()
            plan_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"Trimmed plan to {args.max_clauses} clause(s) for quick test")

    store.add_artifact(
        job_id,
        PipelineStage.plan,
        ArtifactType.plan_json,
        plan_path,
        meta={"source": "test_images_i2v"},
    )
    store.update_job_progress(
        job_id,
        last_completed_stage=PipelineStage.plan,
        clear_error=True,
    )

    orch = PipelineOrchestrator(settings, store)

    if args.images:
        print(f"[images] job={job_id} workflow={settings.comfy_workflow_name}")
        orch.run_images(job_id)
        store.update_job_progress(job_id, last_completed_stage=PipelineStage.images)
        print("[images] done")

    if args.i2v:
        if not settings.i2v_enabled:
            print("ERROR: SHORTS_I2V_ENABLED is false in .env", file=sys.stderr)
            return 1
        print(f"[i2v] job={job_id} workflow={settings.i2v_workflow_name}")
        orch.run_i2v(job_id)
        store.update_job_progress(job_id, last_completed_stage=PipelineStage.i2v)
        print("[i2v] done")

    img_dir = job_dir / "images"
    vid_dir = job_dir / "videos"
    n_img = len(list(img_dir.glob("clause_*.png"))) if img_dir.is_dir() else 0
    n_vid = len(list(vid_dir.glob("clause_*.mp4"))) if vid_dir.is_dir() else 0
    print(f"Output: {n_img} PNG in {img_dir}")
    print(f"Output: {n_vid} MP4 in {vid_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
