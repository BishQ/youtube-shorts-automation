"""Dependency factories for the FastAPI control plane."""

from __future__ import annotations

from shorts_pipeline.config.settings import Settings, get_settings
from shorts_pipeline.jobs.batch_store import BatchStore
from shorts_pipeline.jobs.pipeline_runner import PipelineRunner
from shorts_pipeline.jobs.store import JobStore


def build_store(settings: Settings | None = None) -> JobStore:
    s = settings or get_settings()
    return JobStore(s.data_dir / "jobs.sqlite")


def build_batch_store(settings: Settings | None = None) -> BatchStore:
    s = settings or get_settings()
    return BatchStore(s.data_dir / "jobs.sqlite")


def build_runner(settings: Settings | None = None, store: JobStore | None = None) -> PipelineRunner:
    s = settings or get_settings()
    return PipelineRunner(s, store or build_store(s))
