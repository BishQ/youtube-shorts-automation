"""Tests for LTX 2.3 I2V helpers."""

from shorts_pipeline.video_worker.ltx_i2v import duration_to_ltx_seconds


def test_duration_clamps_to_whole_seconds() -> None:
    assert duration_to_ltx_seconds(4.2) == 5
    assert duration_to_ltx_seconds(0.5) == 2


def test_duration_respects_max() -> None:
    assert duration_to_ltx_seconds(99.0) == 10
