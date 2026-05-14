"""FFmpeg xfade catalog and resolver."""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from shorts_pipeline.editor.models import ClipSpec
from shorts_pipeline.editor.xfade_effects import (
    XFADE_EFFECT_NAMES,
    default_duration_for_xfade_effect,
    pick_random_xfade_effect,
    resolve_xfade_for_clip,
)
from shorts_pipeline.planner.schema import (
    AudioEvent,
    CameraMotion,
    EmotionType,
    SubtitlePosition,
    TransitionType,
)


def _clip(
    *,
    idx: int = 1,
    trans: TransitionType = TransitionType.xfade,
    xfade: str | None = "wipeleft",
) -> ClipSpec:
    return ClipSpec(
        index=idx,
        image_path=Path("/tmp/x.png"),
        duration_s=3.0,
        cut_at_s=1.0,
        camera=CameraMotion.ken_burns,
        transition_in=trans,
        audio_event=AudioEvent.none,
        subtitle_position=SubtitlePosition.bottom,
        emotion=EmotionType.hook,
        xfade_effect_name=xfade,
    )


def test_xfade_catalog_matches_ffmpeg_builtin_count() -> None:
    # ffmpeg -h filter=xfade → custom=-1, fade=0 … revealdown=57 → 58 presets
    assert len(XFADE_EFFECT_NAMES) == 58


def test_resolve_uses_explicit_effect() -> None:
    c = _clip(xfade="circleopen")
    name, dur = resolve_xfade_for_clip(c)
    assert name == "circleopen"
    assert dur == pytest.approx(default_duration_for_xfade_effect("circleopen"))


def test_resolve_hard_cut_zero_duration() -> None:
    c = _clip(trans=TransitionType.hard_cut, xfade=None)
    name, dur = resolve_xfade_for_clip(c)
    assert dur == 0.0


def test_pick_random_is_deterministic_with_seed() -> None:
    rng1 = random.Random(42)
    rng2 = random.Random(42)
    assert pick_random_xfade_effect(rng1) == pick_random_xfade_effect(rng2)


def test_unknown_effect_falls_back_to_fade() -> None:
    c = _clip(xfade="not_a_real_xfade")
    name, _dur = resolve_xfade_for_clip(c)
    assert name == "fade"
