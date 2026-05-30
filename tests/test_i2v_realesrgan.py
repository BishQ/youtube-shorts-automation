"""Real-ESRGAN I2V upscale helpers."""

from __future__ import annotations

from pathlib import Path

from shorts_pipeline.config.settings import Settings
from shorts_pipeline.renderer.i2v_realesrgan import (
    cached_upscaled_clip_path,
    prepare_i2v_paths_for_render,
)


def test_cached_upscaled_path_suffix() -> None:
    src = Path("videos/clause_000.mp4")
    assert cached_upscaled_clip_path(src).as_posix().endswith("realesrgan/clause_000.mp4")


def test_prepare_passthrough_when_ffmpeg_mode(tmp_path: Path) -> None:
    clip = tmp_path / "clause_000.mp4"
    clip.write_bytes(b"x")
    settings = Settings(data_dir=tmp_path, render_i2v_upscale="ffmpeg")
    out = prepare_i2v_paths_for_render([clip], settings=settings)
    assert out[0] == clip
