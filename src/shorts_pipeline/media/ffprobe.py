"""ffprobe invocation for audio/video validation."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


class FFprobeError(Exception):
    pass


def run_ffprobe_json(*, ffprobe_path: str, media_path: Path) -> dict[str, Any]:
    cmd = [
        ffprobe_path,
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(media_path),
    ]
    try:
        proc = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.CalledProcessError as e:
        raise FFprobeError(f"ffprobe failed: {e.stderr or e.stdout}") from e
    except subprocess.TimeoutExpired as e:
        raise FFprobeError("ffprobe timeout") from e
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise FFprobeError("ffprobe returned invalid JSON") from e


def ffprobe_duration_s(*, ffprobe_path: str, media_path: Path) -> float:
    data = run_ffprobe_json(ffprobe_path=ffprobe_path, media_path=media_path)
    fmt = data.get("format", {})
    dur = fmt.get("duration")
    if dur is None:
        raise FFprobeError("no duration in ffprobe output")
    return float(dur)
