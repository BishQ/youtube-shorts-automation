"""Parallax camera mode — zoom + drift zoompan graph."""

from __future__ import annotations

from shorts_pipeline.editor.emotion_to_filtergraph import zoompan_expr
from shorts_pipeline.planner.schema import CameraMotion, EmotionType


def test_parallax_emits_zoompan_with_dual_motion_curves() -> None:
    s = zoompan_expr(
        CameraMotion.parallax,
        n_frames=120,
        w=1080,
        h=1920,
        intensity=0.65,
        clip_index=1,
        emotion=EmotionType.hook,
    )
    assert s.startswith("zoompan=")
    assert ":x='" in s and ":y='" in s
    # Ease-out (z) and smoothstep (x drift) both reference progress on `on`
    assert "pow(1-min(1,on/" in s
    assert "3*pow(" in s
