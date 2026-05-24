"""Tests for editor/pacing.py and editor/__init__.py."""

from pathlib import Path

import pytest

from shorts_pipeline.editor.pacing import (
    MAX_CLIP_DURATION_S,
    MIN_CLIP_DURATION_S,
    compute_cut_times_from_ranges,
    _normalize_ranges,
)
from shorts_pipeline.editor import build_edit_plan_from_ranges
from shorts_pipeline.planner.schema import (
    Beat,
    Clause,
    CameraMotion,
    DecisionLever,
    LutChoice,
    NarrationPlan,
    TransitionType,
)


def _make_plan(n: int = 4) -> NarrationPlan:
    clauses = []
    for i in range(n):
        clauses.append(
            Clause(
                text=f"Clause {i} text here.",
                image_prompt=f"A cinematic wide shot of clause {i} moment, dramatic lighting, "
                f"historical atmosphere, detailed environment, 8k concept art style.",
                beat=Beat(camera=CameraMotion.ken_burns, transition_in=TransitionType.xfade),
            )
        )
    script = " ".join(f"Clause {i} text here." for i in range(n))
    return NarrationPlan.model_construct(
        historical_figure="Caesar",
        cold_open_object="sword",
        decision_lever=DecisionLever(
            lever_type="law",
            description="Crossing the Rubicon was illegal",
            consequence="Civil war began",
        ),
        clauses=clauses,
        full_script=script,
        lut_choice=LutChoice.epic_warm,
        end_plate_question="Would you have crossed?",
    )


def test_normalize_ranges_last_clip_ends_at_narration_duration() -> None:
    # Cut starts spaced by >= MIN_CLIP_DURATION_S so nudge is a no-op
    ranges = [(0.0, 1.0), (2.5, 2.5), (5.0, 4.0), (7.5, 5.0)]
    result = compute_cut_times_from_ranges(ranges, narration_duration_s=10.5)
    assert result[-1][1] == pytest.approx(10.5)


def test_normalize_ranges_cuts_on_start_of_next() -> None:
    ranges = [(0.0, 1.0), (2.6, 2.8), (5.2, 4.5), (7.8, 6.0)]
    result = compute_cut_times_from_ranges(ranges, narration_duration_s=11.0)
    assert result[0][1] == pytest.approx(2.6)
    assert result[1][1] == pytest.approx(5.2)
    assert result[2][1] == pytest.approx(7.8)
    assert result[3][1] == pytest.approx(11.0)


def test_normalize_ranges_no_clip_exceeds_max_duration() -> None:
    """Long silence after last clause start must not yield a >MAX last image."""
    ranges = [(0.0, 1.0), (2.5, 2.5), (5.0, 4.0), (7.5, 5.0)]
    result = compute_cut_times_from_ranges(ranges, narration_duration_s=20.0)
    for i, (a, b) in enumerate(result):
        assert b - a <= MAX_CLIP_DURATION_S + 0.001, f"clip {i} too long: {b - a}"


def test_normalize_ranges_tail_no_micro_clips() -> None:
    """Tail clauses in tiny alignment windows must still get minimum image time."""
    ranges = [
        (0.0, 1.0),
        (48.16, 52.92),
        (53.24, 53.744),
        (53.74999999999999, 54.044),
        (54.05, 54.08),
    ]
    result = compute_cut_times_from_ranges(ranges, narration_duration_s=55.0)
    for i, (a, b) in enumerate(result):
        assert b - a >= MIN_CLIP_DURATION_S - 0.001, f"clip {i} too short: {b - a}"
    assert result[-1][1] == pytest.approx(55.0)


def test_normalize_ranges_minimum_duration() -> None:
    ranges = [(1.0, 1.0)]
    result = _normalize_ranges(ranges, narration_duration_s=5.0)
    assert result[0][1] - result[0][0] >= MIN_CLIP_DURATION_S - 0.001
    assert result[0][1] == pytest.approx(5.0)


def test_build_edit_plan_from_ranges(tmp_path: Path) -> None:
    import wave

    plan = _make_plan(4)

    # Create dummy image + bgm files
    imgs: list[Path] = []
    for i in range(4):
        p = tmp_path / f"img{i}.png"
        p.write_bytes(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06"
            b"\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00"
            b"\x01\r\n-\xdb\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        imgs.append(p)

    bgm = tmp_path / "bgm.wav"
    fr = 8000
    with wave.open(str(bgm), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(fr)
        w.writeframes(b"\x00\x00" * fr * 10)

    ranges = [(0.0, 1.5), (3.0, 3.0), (6.0, 4.5), (9.0, 6.0)]
    narr = 12.0
    edit = build_edit_plan_from_ranges(
        plan, imgs, ranges, narr, bgm, run_face_detection=False
    )

    assert len(edit.clips) == 4
    assert edit.clips[0].cut_at_s == pytest.approx(0.0)
    assert edit.clips[1].cut_at_s == pytest.approx(3.0)
    assert edit.clips[3].duration_s == pytest.approx(3.0)
    assert edit.lut_choice == LutChoice.epic_warm
    assert edit.end_plate_question == "Would you have crossed?"
    assert edit.narration_duration_s == pytest.approx(narr)
