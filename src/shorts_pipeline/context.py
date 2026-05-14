"""Request/job context (structured logging correlation)."""

from contextvars import ContextVar

job_id_var: ContextVar[str | None] = ContextVar("job_id", default=None)


def set_job_id(job_id: str | None) -> None:
    job_id_var.set(job_id)


def get_job_id() -> str | None:
    return job_id_var.get()
