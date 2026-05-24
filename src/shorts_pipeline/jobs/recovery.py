"""Startup recovery for interrupted single-machine pipeline runs."""

from __future__ import annotations

from dataclasses import dataclass

from shorts_pipeline.jobs.models import JobErrorDetail, JobStatus
from shorts_pipeline.jobs.store import JobStore
from shorts_pipeline.logging_setup import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class RecoveredJob:
    job_id: str
    previous_status: JobStatus
    stage: str


def recover_interrupted_jobs(store: JobStore) -> list[RecoveredJob]:
    """Mark jobs left running by a crashed process as failed and unblock the slot.

    A normal operator pause is preserved because it is an intentional blocking
    state. Only `running` jobs are impossible after a clean restart.
    """
    recovered: list[RecoveredJob] = []
    for job in store.list_jobs(limit=10_000):
        if job.status != JobStatus.running:
            continue
        stage = job.current_stage.value if job.current_stage else "unknown"
        store.close_open_stage_timings(job.id)
        store.update_job_progress(
            job.id,
            status=JobStatus.failed,
            error=JobErrorDetail(
                stage=stage,
                code="Interrupted",
                message=(
                    "Job was marked running during startup recovery. "
                    "The previous process likely exited before finishing this stage."
                ),
                detail={"recovered_by": "startup"},
            ),
        )
        recovered.append(RecoveredJob(job.id, job.status, stage))
        log.warning("job_recovered_interrupted", job_id=job.id, stage=stage)
    return recovered
