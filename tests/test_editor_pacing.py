"""Tests for editor/pacing.py and editor/__init__.py."""

from pathlib import Path

import pytest

from shorts_pipeline.editor.pacing import (
    BODY_MAX_DURATION_S,
    BODY_MIN_DURATION_S,
    HOOK_MAX_DURATION_S,
    HOOK_MIN_DURATION_S,
    MAX_CLIP_DURATION_S,
    MIN_CLIP_DURATION_S,
    compute_cut_times_from_ranges,
    compute_i2v_generation_durations_s,
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


def _even_ranges(n: int, narr: float) -> list[tuple[float, float]]:
    """11 evenly spaced clause starts, like a typical TTS alignment."""
    step = narr / n
    return [(i * step, (i + 1) * step) for i in range(n)]


def test_back_compat_constants_map_to_body_band() -> None:
    assert MIN_CLIP_DURATION_S == BODY_MIN_DURATION_S
    assert MAX_CLIP_DURATION_S == BODY_MAX_DURATION_S


def test_last_clip_ends_exactly_at_narration() -> None:
    ranges = _even_ranges(11, 54.0)
    result = compute_cut_times_from_ranges(ranges, narration_duration_s=54.0)
    assert result[-1][1] == pytest.approx(54.0)


def test_clips_tile_the_timeline_back_to_back() -> None:
    ranges = _even_ranges(11, 55.0)
    result = compute_cut_times_from_ranges(ranges, narration_duration_s=55.0)
    assert result[0][0] == pytest.approx(0.0)
    for i in range(1, len(result)):
        assert result[i][0] == pytest.approx(result[i - 1][1])
    total = sum(b - a for a, b in result)
    assert total == pytest.approx(55.0)


def test_hook_held_within_hook_band() -> None:
    ranges = _even_ranges(11, 54.0)
    result = compute_cut_times_from_ranges(ranges, narration_duration_s=54.0)
    hook = result[0][1] - result[0][0]
    assert HOOK_MIN_DURATION_S - 1e-6 <= hook <= HOOK_MAX_DURATION_S + 1e-6


def test_body_clips_within_body_band_when_narration_fits() -> None:
    # 54 s ∈ [43, 57] tiling window → every clip must stay in-band.
    ranges = _even_ranges(11, 54.0)
    result = compute_cut_times_from_ranges(ranges, narration_duration_s=54.0)
    for i in range(1, len(result)):
        dur = result[i][1] - result[i][0]
        assert BODY_MIN_DURATION_S - 1e-6 <= dur <= BODY_MAX_DURATION_S + 1e-6, (
            f"body clip {i} out of band: {dur}"
        )


def test_no_clip_parks_when_narration_overshoots_deck() -> None:
    """Narration longer than the deck's max tiling must not park one image.

    sum_max = 7 + 10×5 = 57 s. At 62 s the 5 s overflow is shared across body
    clips, so no clip balloons to the old 12 s 'frozen last frame' bug.
    """
    ranges = _even_ranges(11, 62.0)
    result = compute_cut_times_from_ranges(ranges, narration_duration_s=62.0)
    durs = [b - a for a, b in result]
    assert result[-1][1] == pytest.approx(62.0)
    # Hook stays at its cap; body overflow (~0.5 s/clip) is shared, not dumped on
    # one image — the last clip must never balloon to the old 12 s frozen frame.
    body_durs = durs[1:]
    assert max(body_durs) <= 6.0, f"a body clip parked: {durs}"
    assert max(body_durs) - min(body_durs) <= 0.5, f"overflow not shared evenly: {durs}"
    assert sum(durs) == pytest.approx(62.0)


def test_hook_kept_clean_under_overflow() -> None:
    # Hook should stay at its max (7) while body clips absorb overflow.
    ranges = _even_ranges(11, 62.0)
    result = compute_cut_times_from_ranges(ranges, narration_duration_s=62.0)
    hook = result[0][1] - result[0][0]
    assert hook == pytest.approx(HOOK_MAX_DURATION_S, abs=0.05)


def test_single_clip_covers_full_narration() -> None:
    result = _normalize_ranges([(1.0, 1.0)], narration_duration_s=5.0)
    assert result[0][0] == pytest.approx(0.0)
    assert result[0][1] == pytest.approx(5.0)


def test_build_edit_plan_from_ranges(tmp_path: Path) -> None:
    import wave

    n = 11
    plan = _make_plan(n)

    imgs: list[Path] = []
    for i in range(n):
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
        w.writeframes(b"\x00\x00" * fr * 60)

    ranges = _even_ranges(n, 54.0)
    narr = 54.0
    edit = build_edit_plan_from_ranges(
        plan, imgs, ranges, narr, bgm, run_face_detection=False
    )

    assert len(edit.clips) == n
    assert edit.clips[0].cut_at_s == pytest.approx(0.0)
    # Hook held in its band; total tiles to narration.
    hook_dur = edit.clips[0].duration_s
    assert HOOK_MIN_DURATION_S - 1e-6 <= hook_dur <= HOOK_MAX_DURATION_S + 1e-6
    assert sum(c.duration_s for c in edit.clips) == pytest.approx(narr, abs=0.01)
    assert edit.lut_choice == LutChoice.epic_warm
    assert edit.end_plate_question == "Would you have crossed?"
    assert edit.narration_duration_s == pytest.approx(narr)


def test_i2v_durations_follow_render_slots_with_margin() -> None:
    ranges = _even_ranges(11, 54.0)
    narr = 54.0
    edit_slots = compute_cut_times_from_ranges(ranges, narr)
    i2v = compute_i2v_generation_durations_s(
        ranges,
        narr,
        margin_s=0.25,
        max_clip_s=7.5,
        last_clip_extra_s=0.8,
    )
    assert len(i2v) == 11
    for (start, end), gen in zip(edit_slots, i2v):
        slot = end - start
        assert gen >= slot
        assert gen <= 7.5 + 1e-6
    # Last clip includes breathe headroom.
    last_slot = edit_slots[-1][1] - edit_slots[-1][0]
    assert i2v[-1] == pytest.approx(min(7.5, last_slot + 0.25 + 0.8), abs=0.01)
    # Body clips should not all park at the old uniform ~5.3 s floor.
    body_gens = i2v[1:]
    assert min(body_gens) < 5.0
    assert max(body_gens) - min(body_gens) > 0.2


def test_i2v_durations_shorter_than_legacy_body_floor() -> None:
    """Short body slots should request less GPU time than the old BODY_MAX+0.3 rule."""
    ranges = _even_ranges(11, 54.0)
    narr = 54.0
    i2v = compute_i2v_generation_durations_s(ranges, narr, margin_s=0.25, max_clip_s=7.5)
    edit_slots = compute_cut_times_from_ranges(ranges, narr)
    short_body_idx = min(
        range(1, 11),
        key=lambda i: edit_slots[i][1] - edit_slots[i][0],
    )
    assert i2v[short_body_idx] < BODY_MAX_DURATION_S + 0.3
