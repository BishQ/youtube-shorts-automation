"""FastAPI lifespan helpers."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI

from shorts_pipeline.config.settings import Settings
from shorts_pipeline.jobs.recovery import recover_interrupted_jobs
from shorts_pipeline.jobs.store import JobStore
from shorts_pipeline.logging_setup import configure_logging
from shorts_pipeline.web.structlog_handler import JobLogHandler


def configure_web_logging(settings: Settings) -> None:
    configure_logging(level=settings.log_level, json_logs=settings.log_json)
    root = logging.getLogger()
    if not any(isinstance(h, JobLogHandler) for h in root.handlers):
        root.addHandler(JobLogHandler())


def build_lifespan(
    *,
    settings: Settings,
    store: JobStore,
    batch_watcher: Callable[[], Awaitable[None]],
):
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_web_logging(settings)
        recover_interrupted_jobs(store)
        task = asyncio.create_task(batch_watcher())
        try:
            yield
        finally:
            task.cancel()

    return lifespan
