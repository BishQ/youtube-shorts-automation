"""Post-render quality gates for Shorts outputs."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shorts_pipeline.config.settings import Settings


class RenderQualityError(RuntimeError):
    """Raised when a rendered video is structurally invalid for publishing."""


@dataclass(frozen=True)
class RenderProbe:
    duration_s: float
    size_bytes: int
    width: int
    height: int
    fps: float
    has_video: bool
    has_audio: bool


def validate_ass_for_render(ass_path: Path) -> None:
    """Reject ASS files that FFmpeg/libass will parse incorrectly."""
    if not ass_path.is_file():
        raise RenderQualityError(f"missing ASS subtitles: {ass_path}")

    text = ass_path.read_text(encoding="utf-8-sig", errors="replace")
    expected = "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
    old = "Format: Layer, Start, End, Style, Name, MarginL, MarginR, Effect, Text"
    if old in text:
        raise RenderQualityError(
            "ASS Events Format is missing MarginV; FFmpeg will render a leading comma"
        )
    if expected not in text:
        raise RenderQualityError("ASS Events Format header is missing or unsupported")

    for line in text.splitlines():
        if not line.startswith("Dialogue:"):
            continue
        fields = line.split(",", 9)
        if len(fields) != 10:
            raise RenderQualityError(f"ASS Dialogue line has {len(fields)} fields, expected 10")
        if fields[9].startswith(","):
            raise RenderQualityError("ASS Dialogue text begins with a comma field separator")


def probe_render(path: Path, settings: Settings) -> RenderProbe:
    """Read structural media metadata with ffprobe."""
    if not path.is_file():
        raise RenderQualityError(f"missing rendered mp4: {path}")
    if path.stat().st_size < 1024:
        raise RenderQualityError(f"rendered mp4 is too small: {path}")

    cmd = [
        settings.ffprobe_path,
        "-v",
        "error",
        "-show_entries",
        "format=duration,size:stream=codec_type,width,height,r_frame_rate",
        "-of",
        "json",
        str(path.resolve()),
    ]
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        raise RenderQualityError(f"ffprobe failed: {proc.stderr[-800:]}")

    try:
        raw: dict[str, Any] = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise RenderQualityError("ffprobe returned invalid JSON") from e

    streams = raw.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None:
        raise RenderQualityError("rendered mp4 has no video stream")
    if audio is None:
        raise RenderQualityError("rendered mp4 has no audio stream")

    fmt = raw.get("format") or {}
    duration_s = float(fmt.get("duration") or 0.0)
    size_bytes = int(fmt.get("size") or path.stat().st_size)
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    fps = _parse_rate(str(video.get("r_frame_rate") or "0/1"))

    return RenderProbe(
        duration_s=duration_s,
        size_bytes=size_bytes,
        width=width,
        height=height,
        fps=fps,
        has_video=True,
        has_audio=True,
    )


def validate_render_output(
    *,
    mp4_path: Path,
    ass_path: Path,
    expected_duration_s: float,
    settings: Settings,
) -> RenderProbe:
    """Run the publish-blocking quality gate after FFmpeg completes."""
    validate_ass_for_render(ass_path)
    probe = probe_render(mp4_path, settings)

    if probe.width != settings.video_width or probe.height != settings.video_height:
        raise RenderQualityError(
            f"wrong video size: {probe.width}x{probe.height}, "
            f"expected {settings.video_width}x{settings.video_height}"
        )

    fps_tolerance = float(getattr(settings, "render_fps_tolerance", 0.25))
    if abs(probe.fps - settings.video_fps) > fps_tolerance:
        raise RenderQualityError(
            f"wrong frame rate: {probe.fps:.3f}, expected {settings.video_fps}"
        )

    tolerance_s = float(getattr(settings, "render_duration_tolerance_s", 0.75))
    delta = abs(probe.duration_s - expected_duration_s)
    if delta > tolerance_s:
        raise RenderQualityError(
            f"wrong duration: {probe.duration_s:.3f}s, expected {expected_duration_s:.3f}s "
            f"(delta {delta:.3f}s)"
        )

    max_duration_s = float(getattr(settings, "render_max_shorts_duration_s", 90.0))
    if probe.duration_s > max_duration_s:
        raise RenderQualityError(
            f"rendered video is too long for Shorts gate: {probe.duration_s:.3f}s "
            f"(max {max_duration_s:.3f}s)"
        )

    min_size_bytes = int(getattr(settings, "render_min_size_bytes", 100_000))
    if probe.size_bytes < min_size_bytes:
        raise RenderQualityError(
            f"rendered mp4 is suspiciously small: {probe.size_bytes} bytes"
        )

    return probe


def _parse_rate(value: str) -> float:
    if "/" not in value:
        return float(value or 0.0)
    num, den = value.split("/", 1)
    denominator = float(den or 1.0)
    if denominator == 0:
        return 0.0
    return float(num or 0.0) / denominator
