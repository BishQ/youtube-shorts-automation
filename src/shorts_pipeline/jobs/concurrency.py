"""Single-machine process lock for pipeline execution."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType


class PipelineLockError(RuntimeError):
    """Raised when another process owns the pipeline lock."""


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


@dataclass
class PipelineProcessLock:
    """Atomic lock file scoped to one data directory.

    The lock prevents accidental double launches from running expensive stages
    against the same SQLite database and artifact directory. Stale lock files
    from dead processes are removed automatically.
    """

    data_dir: Path
    name: str = "pipeline.lock"

    def __post_init__(self) -> None:
        self.path = self.data_dir / self.name
        self._fd: int | None = None

    def acquire(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        try:
            self._fd = os.open(str(self.path), flags)
        except FileExistsError as exc:
            owner = self._read_owner_pid()
            if owner is not None and not _pid_is_running(owner):
                self.path.unlink(missing_ok=True)
                self._fd = os.open(str(self.path), flags)
            else:
                detail = f" by pid {owner}" if owner else ""
                raise PipelineLockError(f"pipeline lock is already held{detail}") from exc
        os.write(self._fd, str(os.getpid()).encode("ascii"))

    def release(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        self.path.unlink(missing_ok=True)

    def _read_owner_pid(self) -> int | None:
        try:
            raw = self.path.read_text(encoding="ascii").strip()
            return int(raw) if raw else None
        except (OSError, ValueError):
            return None

    def __enter__(self) -> "PipelineProcessLock":
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()
