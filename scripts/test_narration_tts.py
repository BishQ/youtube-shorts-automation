"""Test TTS + narration re-plan loop on an existing job (plan.json required).

Usage:
  python scripts/test_narration_tts.py isaac-newton-92085017
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

_env = ROOT / ".env"
if _env.is_file():
    for line in _env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v

from shorts_pipeline.config.settings import Settings
from shorts_pipeline.config.ui_store import effective_settings
from shorts_pipeline.context import set_job_id
from shorts_pipeline.jobs.image_recovery import ensure_plan_artifact_from_disk
from shorts_pipeline.jobs.models import ArtifactType, JobConfigSnapshot, JobStatus, PipelineStage
from shorts_pipeline.jobs.store import JobStore
from shorts_pipeline.jobs.paths import niche_job_dir, niche_folder_name
from shorts_pipeline.media.ffprobe import ffprobe_duration_s
from shorts_pipeline.orchestrator import PipelineOrchestrator


def main() -> int:
    job_id = sys.argv[1] if len(sys.argv) > 1 else None
    if not job_id:
        print("usage: python scripts/test_narration_tts.py <job_id>")
        return 1

    settings = effective_settings(Settings())
    store = JobStore(settings.data_dir / "jobs.sqlite")
    set_job_id(job_id)

    rec = store.get_job(job_id)
    if rec is None:
        for niche_dir in (settings.data_dir / "jobs").iterdir():
            jd = niche_dir / job_id if niche_dir.is_dir() else None
            if jd and jd.is_dir() and (jd / "plan.json").is_file():
                plan = json.loads((jd / "plan.json").read_text(encoding="utf-8"))
                cfg = JobConfigSnapshot(
                    figure_name=plan.get("historical_figure") or job_id,
                    bgm_path=str(settings.data_dir / "bgm.wav"),
                    topic_type="documentary",
                )
                store.import_job_if_missing(job_id, cfg)
                break
        rec = store.get_job(job_id)
    if rec is None:
        print(f"job not found: {job_id}")
        return 1

    ensure_plan_artifact_from_disk(store, settings, job_id)
    store.delete_artifacts_from_stage(job_id, PipelineStage.tts)
    store.update_job_progress(
        job_id,
        status=JobStatus.running,
        current_stage=PipelineStage.tts,
        last_completed_stage=PipelineStage.plan,
        clear_error=True,
    )

    orch = PipelineOrchestrator(settings, store)
    print(f"Running TTS + re-plan loop for {job_id} ...")
    print(f"  replan threshold: {settings.narration_replan_threshold_s}s")
    print(f"  fit target: {settings.narration_fit_target_s}s  max speed: {settings.narration_fit_atempo_max}x")
    orch.run_tts(job_id)

    wav = niche_job_dir(settings, niche_folder_name(rec.topic_type), job_id) / "narration.wav"
    if not wav.is_file():
        for p in (settings.data_dir / "jobs").rglob(job_id):
            cand = p / "narration.wav"
            if cand.is_file():
                wav = cand
                break

    dur = ffprobe_duration_s(ffprobe_path=settings.ffprobe_path, media_path=wav)
    art = store.get_latest_artifact(job_id, PipelineStage.tts, ArtifactType.narration_wav)
    meta = {}
    if art and art.meta_json:
        meta = json.loads(art.meta_json)

    print("\n=== Result ===")
    print(f"  narration.wav : {dur:.2f}s")
    print(f"  replans       : {meta.get('narration_replan_count', 0)}")
    print(f"  raw TTS       : {meta.get('narration_raw_s', '?')}s")
    print(f"  fit action    : {meta.get('narration_fit_action', '?')}")
    print(f"  atempo        : {meta.get('narration_atempo', '?')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
