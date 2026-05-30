"""Single canonical batch scheduler used by the web API."""

from __future__ import annotations

from pathlib import Path

from shorts_pipeline.config.settings import Settings
from shorts_pipeline.jobs.batch_store import BatchRecord, BatchStore
from shorts_pipeline.jobs.models import ArtifactType, JobConfigSnapshot, JobStatus, PipelineStage
from shorts_pipeline.jobs.pipeline_runner import PipelineRunner
from shorts_pipeline.jobs.store import JobStore
from shorts_pipeline.logging_setup import get_logger

log = get_logger(__name__)


class BatchScheduler:
    """Advance one active batch while preserving current_index as the active topic."""

    def __init__(
        self,
        *,
        settings: Settings,
        store: JobStore,
        batch_store: BatchStore,
        runner: PipelineRunner,
    ) -> None:
        self._settings = settings
        self._store = store
        self._batch_store = batch_store
        self._runner = runner

    def tick_active(self) -> None:
        batch = self._batch_store.get_active()
        if batch is None or batch.status != "running":
            return
        self.tick(batch)

    def tick(self, batch: BatchRecord) -> None:
        if self._store.has_running_job():
            return
        idx = batch.current_index
        if idx >= len(batch.topics):
            self._batch_store.set_status(batch.id, "completed")
            log.info("batch_completed", batch_id=batch.id, total=len(batch.topics))
            return

        existing_job_id = batch.job_ids[idx] if idx < len(batch.job_ids) else None
        if existing_job_id:
            self._handle_existing_job(batch, idx, existing_job_id)
            return

        topic = batch.topics[idx]
        duplicate_job_id = self._check_topic_duplicate(topic)
        if duplicate_job_id:
            self._batch_store.record_job(batch.id, idx, duplicate_job_id)
            self._batch_store.set_current_index(batch.id, idx + 1)
            log.info(
                "batch_topic_skipped",
                batch_id=batch.id,
                topic=topic,
                index=idx,
                job_id=duplicate_job_id,
            )
            return

        bgm = Path(batch.bgm_path)
        if not bgm.is_file():
            self._batch_store.set_status(batch.id, "paused")
            log.error("batch_paused_bgm_missing", batch_id=batch.id, bgm=str(bgm))
            return

        effective = self._runner.effective_settings()
        cfg = JobConfigSnapshot(
            figure_name=topic,
            bgm_path=str(bgm.resolve()),
            niche=batch.topic_type,
            topic_type=batch.topic_type,
            language=batch.language,
            watermark_enabled=effective.watermark_enabled,
            end_plate_enabled=effective.end_plate_enabled,
            comfy_workflow_name=effective.comfy_workflow_name,
            overlay_enabled=effective.overlay_enabled,
        )
        job_id = self._store.create_job(cfg)
        self._batch_store.record_job(batch.id, idx, job_id)
        future = self._runner.start_job(job_id)
        future.add_done_callback(lambda f: self._log_background_failure(f, job_id))
        log.info("batch_job_started", batch_id=batch.id, topic=topic, index=idx, job_id=job_id)

    def start(self, batch_id: str, from_index: int) -> None:
        batch = self._batch_store.get(batch_id)
        if batch is None:
            return
        idx = max(0, min(from_index, max(0, len(batch.topics) - 1)))
        self._batch_store.clear_job_ids_from(batch_id, idx)
        self._batch_store.set_index_and_status(batch_id, idx, "running")
        if not self._store.has_running_job():
            active = self._batch_store.get(batch_id)
            if active is not None:
                self.tick(active)

    def _handle_existing_job(self, batch: BatchRecord, idx: int, job_id: str) -> None:
        job = self._store.get_job(job_id)
        if job is None:
            self._batch_store.clear_job_ids_from(batch.id, idx)
            return
        if job.status == JobStatus.completed:
            self._batch_store.set_current_index(batch.id, idx + 1)
            log.info("batch_topic_completed", batch_id=batch.id, job_id=job_id, index=idx)
            return
        if job.status == JobStatus.failed:
            self._batch_store.set_status(batch.id, "paused")
            log.warning("batch_auto_paused", reason="job_failed", batch_id=batch.id, job_id=job_id)
            return
        if job.status in (JobStatus.pending, JobStatus.paused):
            future = self._runner.start_job(job_id)
            future.add_done_callback(lambda f: self._log_background_failure(f, job_id))

    def _check_topic_duplicate(self, figure_name: str) -> str | None:
        skip_job_id: str | None = None
        existing = self._store.find_jobs_by_figure(figure_name)
        for job in existing:
            for art_type in (ArtifactType.final_mp4, ArtifactType.final_wan_mp4):
                art = self._store.get_latest_artifact(
                    job.id, PipelineStage.render, art_type
                )
                if art and Path(art.path).is_file() and Path(art.path).stat().st_size > 0:
                    skip_job_id = job.id
                    break
            if skip_job_id is not None:
                break
        if skip_job_id is not None:
            return skip_job_id
        for job in existing:
            if job.status == JobStatus.failed:
                self._store.delete_job(job.id)
        return None

    @staticmethod
    def _log_background_failure(future: object, job_id: str) -> None:
        try:
            result = getattr(future, "result")
            result()
        except BaseException as exc:
            log.warning("background_pipeline_failed", job_id=job_id, error=str(exc)[:400])
