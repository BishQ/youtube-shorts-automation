"""Cooperative cancellation helpers for long-running pipeline stages."""

from __future__ import annotations

from shorts_pipeline.jobs.exceptions import CancelledJobError
from shorts_pipeline.jobs.models import JobStatus
from shorts_pipeline.jobs.store import JobStore


def is_cancelled(store: JobStore, job_id: str) -> bool:
    """Return True when the persisted job row represents an operator cancellation."""
    job = store.get_job(job_id)
    if job is None or job.status != JobStatus.failed or job.error is None:
        return False
    return job.error.code == "Cancelled"


def raise_if_cancelled(store: JobStore, job_id: str) -> None:
    """Raise a control exception if the operator cancelled this job."""
    if is_cancelled(store, job_id):
        raise CancelledJobError(f"job {job_id} was cancelled")
