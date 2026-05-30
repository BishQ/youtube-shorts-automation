"""Tests for narration replan feedback helper."""

from shorts_pipeline.config.settings import Settings
from shorts_pipeline.planner.schema import Beat, Clause, NarrationPlan
from shorts_pipeline.tts_worker.narration_replan import (
    build_narration_too_long_feedback,
    narration_replan_threshold_s,
)


def _minimal_plan() -> NarrationPlan:
    beat = Beat(
        emotion="hook",
        intensity=0.8,
        camera="ken_burns",
        transition_in="hard_cut",
        duration_hint="medium",
        color_grade="dark_thriller",
        audio_event="none",
        emphasis_words=["test"],
        subtitle_position="middle",
        cut_target="test",
        visual_tier="cinematic",
    )
    clauses = [
        Clause(
            text=f"Clause number {i} with enough words here.",
            image_prompt=(
                f"cinematic documentary still frame number {i}, dramatic natural "
                f"lighting, shallow depth of field, no text"
            ),
            beat=beat,
        )
        for i in range(11)
    ]
    return NarrationPlan(
        historical_figure="Isaac Newton",
        cold_open_object="apple",
        decision_lever={
            "lever_type": "science",
            "description": "Gravity insight",
            "consequence": "Changed physics forever.",
        },
        clauses=clauses,
        full_script=" ".join(c.text for c in clauses),
        end_plate_question="What would you have done?",
    )


def test_threshold_is_65s() -> None:
    s = Settings()
    assert narration_replan_threshold_s(s) == 65.0


def test_feedback_states_seconds_over_65_limit() -> None:
    plan = _minimal_plan()
    s = Settings(narration_replan_threshold_s=65.0, narration_fit_target_s=59.0)
    msg = build_narration_too_long_feedback(plan, measured_s=69.0, settings=s, attempt=1)
    assert "4.0 seconds over the 65s limit" in msg
    assert "measured 69.0s" in msg
    assert "near 59s" in msg
