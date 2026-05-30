"""Render stage adapter."""

from __future__ import annotations

from shorts_pipeline.logging_setup import get_logger
from shorts_pipeline.orchestrator import PipelineOrchestrator

log = get_logger(__name__)


def run(orchestrator: PipelineOrchestrator, job_id: str) -> None:
    """Mux final Short(s).

    When i2v + render_dual_outputs: Ken Burns ``final.mp4`` then Wan ``final_wan.mp4``.
    Otherwise a single ``auto`` render (Wan clips used when present).
    """
    settings = orchestrator._settings
    if settings.i2v_enabled and settings.render_dual_outputs:
        orchestrator.run_render(job_id, visual_mode="ken_burns")
        plan = orchestrator._load_plan(job_id)
        video_paths = orchestrator._load_i2v_video_paths(job_id, plan)
        if all(p is not None and p.is_file() for p in video_paths):
            out = orchestrator._job_dir(job_id) / "final_wan.mp4"
            orchestrator.run_render(
                job_id,
                visual_mode="wan",
                out_mp4=out,
            )
        else:
            missing = [i for i, p in enumerate(video_paths) if p is None or not p.is_file()]
            log.warning(
                "wan_render_skipped_missing_i2v",
                job_id=job_id,
                missing=missing,
            )
    else:
        orchestrator.run_render(job_id)
