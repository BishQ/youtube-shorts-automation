"""Dependency readiness checks for the local production pipeline."""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

from shorts_pipeline.config.settings import Settings


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str = ""


@dataclass(frozen=True)
class PreflightReport:
    ok: bool
    checks: list[CheckResult]

    def model_dump(self) -> dict[str, object]:
        return {"ok": self.ok, "checks": [asdict(c) for c in self.checks]}


class PreflightError(RuntimeError):
    """Raised when required local dependencies are not ready."""

    def __init__(self, report: PreflightReport) -> None:
        self.report = report
        failed = ", ".join(c.name for c in report.checks if not c.ok)
        super().__init__(f"preflight checks failed: {failed}")


def _check_sqlite(db_path: Path) -> CheckResult:
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(db_path, timeout=5.0) as conn:
            conn.execute("SELECT 1").fetchone()
        return CheckResult("sqlite", True, str(db_path))
    except Exception as exc:
        return CheckResult("sqlite", False, str(exc))


def _check_data_dir(data_dir: Path) -> CheckResult:
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        probe = data_dir / ".write_test"
        probe.write_text("ok", encoding="ascii")
        probe.unlink(missing_ok=True)
        return CheckResult("data_dir", True, str(data_dir.resolve()))
    except Exception as exc:
        return CheckResult("data_dir", False, str(exc))


def _check_executable(name: str, executable: str) -> CheckResult:
    resolved = shutil.which(executable) or executable
    try:
        result = subprocess.run(
            [resolved, "-version"],
            capture_output=True,
            text=True,
            timeout=8,
        )
    except Exception as exc:
        return CheckResult(name, False, str(exc))
    if result.returncode != 0:
        return CheckResult(name, False, (result.stderr or result.stdout)[-300:])
    first = (result.stdout or result.stderr).splitlines()[0] if (result.stdout or result.stderr) else resolved
    return CheckResult(name, True, first[:300])


def _check_http_json(name: str, url: str, *, timeout_s: float = 3.0) -> CheckResult:
    try:
        with httpx.Client(timeout=timeout_s) as client:
            response = client.get(url)
        if response.status_code >= 400:
            return CheckResult(name, False, f"HTTP {response.status_code}: {response.text[:200]}")
        return CheckResult(name, True, f"HTTP {response.status_code}")
    except Exception as exc:
        return CheckResult(name, False, str(exc))


def run_preflight(settings: Settings, *, include_optional: bool = False) -> PreflightReport:
    """Run local dependency checks without mutating pipeline state."""
    checks = [
        _check_data_dir(settings.data_dir),
        _check_sqlite(settings.data_dir / "jobs.sqlite"),
        _check_executable("ffmpeg", settings.ffmpeg_path),
        _check_executable("ffprobe", settings.ffprobe_path),
        _check_http_json("vllm", settings.local_llm_base_url.rstrip("/") + "/models"),
        _check_http_json("comfyui", settings.comfy_base_url.rstrip("/") + "/system_stats"),
    ]
    if include_optional and settings.tts_backend.lower().strip() == "kokoro_http":
        checks.append(_check_http_json("kokoro_http", settings.kokoro_http_base_url.rstrip("/") + "/health"))
    return PreflightReport(ok=all(c.ok for c in checks), checks=checks)


def require_preflight(settings: Settings, *, include_optional: bool = False) -> PreflightReport:
    report = run_preflight(settings, include_optional=include_optional)
    if not report.ok:
        raise PreflightError(report)
    return report
