"""Tests for Wan I2V motion prompt resolution."""

from __future__ import annotations

from shorts_pipeline.planner.schema import (
    Beat,
    CameraMotion,
    Clause,
    EmotionType,
    TransitionType,
)
from shorts_pipeline.video_worker.motion_resolver import resolve_motion_prompt


def _clause(
    *,
    motion: str = "",
    camera: CameraMotion = CameraMotion.ken_burns,
    emotion: EmotionType = EmotionType.hook,
) -> Clause:
    return Clause(
        text="He turned toward the fire.",
        image_prompt=(
            "Hero close-up portrait, weathered face dominant in frame, torchlit dusk "
            "light from camera-left, dramatic chiaroscuro, shallow depth of field."
        ),
        motion_prompt=motion,
        beat=Beat(
            emotion=emotion,
            intensity=0.8,
            camera=camera,
            transition_in=TransitionType.hard_cut,
        ),
    )


def test_uses_explicit_motion_prompt_when_valid() -> None:
    explicit = (
        "camera slow push-in, subject's jaw tightens, embers drift upward in warm light"
    )
    out = resolve_motion_prompt(_clause(motion=explicit), niche="history")
    assert out == explicit


def test_falls_back_to_camera_and_emotion_for_history() -> None:
    out = resolve_motion_prompt(
        _clause(camera=CameraMotion.zoom_out, emotion=EmotionType.tragic),
        niche="history",
    )
    assert "pull-back" in out.lower()
    assert "head" in out.lower() or "lowers" in out.lower()


def test_generic_fallback_without_niche() -> None:
    out = resolve_motion_prompt(_clause(), niche=None)
    assert "push-in" in out.lower()
    assert len(out) >= 20
