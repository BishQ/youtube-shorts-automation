"""Canonical pipeline execution service."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass

from shorts_pipeline.config.settings import Settings
from shorts_pipeline.config.ui_store import effective_settings
from shorts_pipeline.context import set_job_id
from shorts_pipeline.jobs.cancellation import raise_if_cancelled
from shorts_pipeline.jobs.concurrency import PipelineProcessLock
from shorts_pipeline.jobs.preflight import require_preflight
from shorts_pipeline.jobs.state_machine import StageRunner
from shorts_pipeline.jobs.store import JobStore
from shorts_pipeline.logging_setup import get_logger
from shorts_pipeline.orchestrator import PipelineOrchestrator, orchestrator_stage_handler

log = get_logger(__name__)


@dataclass(frozen=True)
class PipelineRunOptions:
    preflight: bool = False


class PipelineRunner:
    """Single API for running pipeline work from web, batch, or CLI entry points."""

    def __init__(self, settings: Settings, store: JobStore, *, max_workers: int = 1) -> None:
        self._base_settings = settings
        self._store = store
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="shorts-pipeline")

    def effective_settings(self) -> Settings:
        return effective_settings(self._base_settings)

    def run_job(self, job_id: str, options: PipelineRunOptions | None = None) -> None:
        """Run a full resumable job synchronously in the current thread."""
        opts = options or PipelineRunOptions()
        set_job_id(job_id)
        settings = self.effective_settings()
        if opts.preflight:
            require_preflight(settings)
        with PipelineProcessLock(settings.data_dir):
            raise_if_cancelled(self._store, job_id)
            handler = orchestrator_stage_handler(settings, self._store)
            StageRunner(self._store, handler, settings=settings).resume_job(job_id)

    def start_job(self, job_id: str, options: PipelineRunOptions | None = None) -> Future[None]:
        """Submit a full pipeline job to the runner's single-worker executor."""
        log.info("pipeline_job_submitted", job_id=job_id)
        return self._executor.submit(self.run_job, job_id, options)

    def regenerate_script(self, job_id: str, *, user_feedback: str | None = None) -> None:
        """Regenerate the script, then resume from the correct downstream stage."""
        set_job_id(job_id)
        settings = self.effective_settings()
        with PipelineProcessLock(settings.data_dir):
            raise_if_cancelled(self._store, job_id)
            PipelineOrchestrator(settings, self._store).run_rescript(
                job_id,
                user_feedback=user_feedback,
            )
            handler = orchestrator_stage_handler(settings, self._store)
            StageRunner(self._store, handler, settings=settings).resume_job(job_id)

    def start_regenerate_script(self, job_id: str, *, user_feedback: str | None = None) -> Future[None]:
        return self._executor.submit(self.regenerate_script, job_id, user_feedback=user_feedback)

    def regenerate_publish_package(self, job_id: str) -> None:
        set_job_id(job_id)
        settings = self.effective_settings()
        PipelineOrchestrator(settings, self._store).run_publish(job_id)

    def start_regenerate_publish_package(self, job_id: str) -> Future[None]:
        return self._executor.submit(self.regenerate_publish_package, job_id)

    def shutdown(self, *, wait: bool = False) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=False)
