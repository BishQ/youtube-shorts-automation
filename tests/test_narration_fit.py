"""Tests for the duration auto-fit policy (tts_worker/narration_fit.plan_narration_fit)."""

import pytest

from shorts_pipeline.config.settings import Settings
from shorts_pipeline.tts_worker.narration_fit import plan_narration_fit


def _settings(**over) -> Settings:
    base = dict(
        narration_fit_enabled=True,
        narration_fit_target_s=59.0,
        narration_fit_band_min_s=57.0,
        narration_fit_band_max_s=59.0,
        narration_fit_atempo_min=0.9,
        narration_fit_atempo_max=1.15,
        narration_fit_hard_cap_s=59.0,
    )
    base.update(over)
    return Settings(**base)


def test_in_band_is_noop() -> None:
    p = plan_narration_fit(58.0, _settings())
    assert p.action == "in_band"
    assert p.atempo == 1.0


def test_up_to_65s_uses_mild_speedup() -> None:
    # 62s → 1.05x speedup toward 59s (within 1.15 ceiling).
    p = plan_narration_fit(62.0, _settings())
    assert p.atempo == pytest.approx(62.0 / 59.0, abs=0.01)
    assert p.action == "stretch"
    assert p.predicted_s == pytest.approx(59.0, abs=0.5)


def test_65s_ceiling_fits_with_speedup() -> None:
    p = plan_narration_fit(65.0, _settings())
    assert p.atempo == pytest.approx(65.0 / 59.0, abs=0.01)
    assert p.action == "stretch"
    assert p.predicted_s == pytest.approx(59.0, abs=0.5)


def test_over_65s_would_hard_cut_if_not_replanned() -> None:
    # 69s: orchestrator re-plans before fit; if it reached fit, 1.15x → 60s → cut.
    p = plan_narration_fit(69.0, _settings())
    assert p.atempo == pytest.approx(1.15)
    assert p.action == "hard_cut"
    assert p.predicted_s == pytest.approx(59.0)


def test_short_script_is_slowed_down() -> None:
    p = plan_narration_fit(52.0, _settings())
    assert p.atempo == pytest.approx(0.9)
    assert p.predicted_s == pytest.approx(57.8, abs=0.2)
    assert p.action == "stretch"


def test_very_long_script_hard_cut() -> None:
    p = plan_narration_fit(80.0, _settings())
    assert p.atempo == pytest.approx(1.15)
    assert p.action == "hard_cut"
    assert p.predicted_s == pytest.approx(59.0)
