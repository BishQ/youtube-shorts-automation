"""Tests for render upscale + unsharp filter helpers."""

from __future__ import annotations

from pathlib import Path

from shorts_pipeline.config.settings import Settings
from shorts_pipeline.renderer.video_enhance import (
    i2v_upscale_filter_chain,
    unsharp_filter_suffix,
)


def test_i2v_chain_two_step_unsharp_and_spline() -> None:
    s = Settings(
        render_i2v_two_step_upscale=True,
        render_unsharp_enabled=True,
        render_unsharp_luma_amount=0.45,
    )
    chain = i2v_upscale_filter_chain(w=1080, h=1920, fps=30, duration_s=4.2, settings=s)
    assert "scale=iw*2:ih*2:flags=spline" in chain
    assert "scale=1080:1920" in chain
    assert "unsharp=" in chain
    assert "luma_amount=0.4500" in chain


def test_i2v_chain_single_step_without_unsharp() -> None:
    s = Settings(
        render_i2v_two_step_upscale=False,
        render_unsharp_enabled=False,
    )
    chain = i2v_upscale_filter_chain(w=1080, h=1920, fps=30, duration_s=3.0, settings=s)
    assert "iw*2" not in chain
    assert "unsharp=" not in chain


def test_unsharp_size_is_odd() -> None:
    s = Settings(render_unsharp_enabled=True, render_unsharp_luma_size=4)
    suffix = unsharp_filter_suffix(s)
    assert "luma_msize_x=5" in suffix
