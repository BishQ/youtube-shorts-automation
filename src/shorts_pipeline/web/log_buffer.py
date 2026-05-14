"""In-memory structured log ring buffer per job (for SSE / polling)."""

from __future__ import annotations

from collections import defaultdict, deque
from threading import Lock
from typing import Any

_MAX = 500


class JobLogBuffer:
    def __init__(self, max_lines: int = _MAX) -> None:
        self._max = max_lines
        self._lock = Lock()
        self._buf: dict[str, deque[dict[str, Any]]] = defaultdict(lambda: deque(maxlen=max_lines))

    def append(self, job_id: str, record: dict[str, Any]) -> None:
        with self._lock:
            self._buf[job_id].append(record)

    def tail(self, job_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            q = self._buf.get(job_id)
            if not q:
                return []
            items = list(q)[-limit:]
            return items


job_log_buffer = JobLogBuffer()
