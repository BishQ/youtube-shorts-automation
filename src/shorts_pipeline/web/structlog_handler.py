"""Bridge structlog/stdlib logging to JobLogBuffer."""

from __future__ import annotations

import logging
from typing import Any

from shorts_pipeline.context import get_job_id
from shorts_pipeline.web.log_buffer import job_log_buffer


class JobLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            jid = get_job_id()
            if not jid:
                return
            payload: dict[str, Any] = {
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            }
            if record.exc_info:
                payload["exc_info"] = self.format(record)
            job_log_buffer.append(jid, payload)
        except Exception:
            self.handleError(record)
