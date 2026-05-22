"""Idempotent stage execution and resume."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from shorts_pipeline.context import set_job_id
from shorts_pipeline.config.settings import Settings
from shorts_pipeline.jobs.exceptions import CooperativePauseError
from shorts_pipeline.jobs.models import (
    ArtifactType,
    JobErrorDetail,
    JobStatus,
    PipelineStage,
    next_stage,
)
from shorts_pipeline.jobs.stage_timing import timing_label
from shorts_pipeline.jobs.store import JobStore, verify_artifact_path
from shorts_pipeline.logging_setup import get_logger

log = get_logger(__name__)


class StageHandler(Protocol):
    """Implement one method per stage; return paths / side effects via store."""

    def run_plan(self, job_id: str) -> None: ...
    def run_images(self, job_id: str) -> None: ...
    def run_tts(self, job_id: str) -> None: ...
    def run_align(self, job_id: str) -> None: ...
    def run_i2v(self, job_id: str) -> None: ...
    def run_render(self, job_id: str) -> None: ...
    def run_publish(self, job_id: str) -> None: ...


class StageRunner:
    def __init__(
        self,
        store: JobStore,
        handler: StageHandler,
        *,
        settings: Settings | None = None,
    ) -> None:
        self._store = store
        self._handler = handler
        self._settings = settings

    def _i2v_enabled(self) -> bool:
        if self._settings is None:
            return False
        return bool(self._settings.i2v_enabled)

    def _gate_complete(self, job_id: str, stage: PipelineStage) -> bool:
        if stage == PipelineStage.plan:
            art = self._store.get_latest_artifact(job_id, stage, ArtifactType.plan_json)
            if art is None:
                return False
            return verify_artifact_path(Path(art.path), art.sha256)
        if stage == PipelineStage.images:
            arts = self._store.get_artifacts_for_stage(job_id, stage, ArtifactType.image_png)
            if not arts:
                return False
            expected = self._expected_image_count(job_id)
            if expected is not None and len(arts) != expected:
                return False
            return all(verify_artifact_path(Path(a.path), a.sha256) for a in arts)
        if stage == PipelineStage.tts:
            art = self._store.get_latest_artifact(job_id, stage, ArtifactType.narration_wav)
            if art is None:
                return False
            return verify_artifact_path(Path(art.path), art.sha256)
        if stage == PipelineStage.align:
            art = self._store.get_latest_artifact(job_id, stage, ArtifactType.subtitles_ass)
            if art is None:
                return False
            return verify_artifact_path(Path(art.path), art.sha256)
        if stage == PipelineStage.i2v:
            if not self._i2v_enabled():
                return True
            arts = self._store.get_artifacts_for_stage(job_id, stage, ArtifactType.video_mp4)
            if not arts:
                return False
            expected = self._expected_image_count(job_id)
            if expected is not None and len(arts) != expected:
                return False
            return all(verify_artifact_path(Path(a.path), a.sha256) for a in arts)
        if stage == PipelineStage.render:
            art = self._store.get_latest_artifact(job_id, stage, ArtifactType.final_mp4)
            if art is None:
                return False
            return verify_artifact_path(Path(art.path), art.sha256)
        if stage == PipelineStage.publish:
            art = self._store.get_latest_artifact(
                job_id, stage, ArtifactType.publish_package_json
            )
            if art is None:
                return False
            return verify_artifact_path(Path(art.path), art.sha256)
        return False

    def _expected_image_count(self, job_id: str) -> int | None:
        art = self._store.get_latest_artifact(job_id, PipelineStage.plan, ArtifactType.plan_json)
        if art is None:
            return None
        p = Path(art.path)
        if not verify_artifact_path(p, art.sha256):
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None
        clauses = data.get("clauses")
        if isinstance(clauses, list):
            return len(clauses)
        return None

    def resume_job(self, job_id: str) -> None:
        set_job_id(job_id)
        record = self._store.get_job(job_id)
        if record is None:
            raise ValueError(f"unknown job_id={job_id}")
        if record.status == JobStatus.completed:
            log.info("job_already_completed", job_id=job_id)
            return

        self._store.update_job_progress(
            job_id,
            status=JobStatus.running,
            clear_error=True,
        )

        stage_order = list(PipelineStage)
        start_idx = 0
        if record.last_completed_stage is not None:
            start_idx = stage_order.index(record.last_completed_stage) + 1

        try:
            for stage in stage_order[start_idx:]:
                if self._gate_complete(job_id, stage):
                    log.info("stage_skip_idempotent", stage=stage.value)
                    self._store.record_stage_skip(job_id, stage, timing_label(stage))
                    self._store.update_job_progress(
                        job_id,
                        current_stage=next_stage(stage) or stage,
                        last_completed_stage=stage,
                    )
                    continue

                self._store.update_job_progress(job_id, current_stage=stage)
                log.info("stage_start", stage=stage.value)
                # Dispatch by convention: every PipelineStage value maps to
                # handler.run_<stage_value>. Looking the method up lazily keeps
                # mocks and partial handlers usable when only a subset of stages
                # is exercised.
                method = getattr(self._handler, f"run_{stage.value}", None)
                if method is None or not callable(method):
                    raise RuntimeError(
                        f"stage handler is missing required method run_{stage.value}"
                    )
                self._store.record_stage_start(job_id, stage, timing_label(stage))
                try:
                    method(job_id)
                finally:
                    self._store.record_stage_end(job_id)

                if not self._gate_complete(job_id, stage):
                    raise RuntimeError(
                        f"stage {stage.value} finished without valid artifacts"
                    )

                nxt = next_stage(stage)
                self._store.update_job_progress(
                    job_id,
                    last_completed_stage=stage,
                    current_stage=nxt if nxt else stage,
                )
                log.info("stage_complete", stage=stage.value)

            self._store.update_job_progress(
                job_id,
                status=JobStatus.completed,
                last_completed_stage=PipelineStage.publish,
                clear_current_stage=True,
            )
        except CooperativePauseError:
            log.info("pipeline_cooperative_pause", job_id=job_id)
            self._store.update_job_progress(
                job_id,
                status=JobStatus.paused,
                clear_error=True,
            )
            return
        except Exception as e:
            log.exception("pipeline_failed", error=str(e))
            job = self._store.get_job(job_id)
            current = job.current_stage if job else None
            self._store.update_job_progress(
                job_id,
                status=JobStatus.failed,
                error=JobErrorDetail(
                    stage=current.value if current else "unknown",
                    code=type(e).__name__,
                    message=str(e),
                    detail=None,
                ),
            )
            raise
