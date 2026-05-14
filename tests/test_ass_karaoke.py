from pathlib import Path

from shorts_pipeline.aligner.ass import build_ass_karaoke
from shorts_pipeline.aligner.base import WordSpan
from shorts_pipeline.config.settings import Settings
from shorts_pipeline.planner.schema import (
    Beat,
    Clause,
    DecisionLever,
    EmotionType,
    NarrationPlan,
    SubtitlePosition,
    TransitionType,
)


_LONG = (
    "A cinematic wide shot, dramatic lighting, historical atmosphere, "
    "detailed environment, 8k concept art style."
)


def _make_plan(*, emphasis: list[str] | None = None, position: SubtitlePosition = SubtitlePosition.bottom) -> NarrationPlan:
    beat = Beat(
        emotion=EmotionType.hook,
        emphasis_words=emphasis or [],
        subtitle_position=position,
        transition_in=TransitionType.hard_cut,
    )
    return NarrationPlan.model_construct(
        historical_figure="Test",
        cold_open_object="tablet",
        decision_lever=DecisionLever(
            lever_type="law",
            description="A law changed who could own land",
            consequence="It concentrated power in the capital.",
        ),
        clauses=[
            Clause(text="First line here.", image_prompt=f"Tablet closeup, {_LONG}", beat=beat),
            Clause(text="Second line there.", image_prompt=f"Archive hall interior, {_LONG}"),
            Clause(text="Third beat lands.", image_prompt=f"City skyline at dusk, {_LONG}"),
            Clause(text="Fourth closes out.", image_prompt=f"Documents burning, {_LONG}"),
        ],
        full_script="First line here. Second line there. Third beat lands. Fourth closes out.",
        lut_choice=None,
        end_plate_question=None,
    )


_WORDS = [
    WordSpan("First", 0.0, 0.15),
    WordSpan("line", 0.15, 0.25),
    WordSpan("here.", 0.25, 0.35),
    WordSpan("Second", 0.35, 0.45),
    WordSpan("line", 0.45, 0.55),
    WordSpan("there.", 0.55, 0.65),
    WordSpan("Third", 0.65, 0.75),
    WordSpan("beat", 0.75, 0.82),
    WordSpan("lands.", 0.82, 0.9),
    WordSpan("Fourth", 0.9, 0.95),
    WordSpan("closes", 0.95, 1.05),
    WordSpan("out.", 1.05, 1.15),
]


def test_build_ass_contains_karaoke_and_dialogue(tmp_path: Path) -> None:
    plan = _make_plan()
    out = tmp_path / "out.ass"
    build_ass_karaoke(plan, _WORDS, Settings(), out)
    txt = out.read_text(encoding="utf-8")
    assert "Dialogue:" in txt
    assert "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text" in txt
    assert ",,{" in txt
    # Per-word highlight uses transform scale, not classic {\\kf} timing.
    assert r"\fscx112" in txt and r"\t(0,210,\fscx100\fscy100)" in txt
    assert r"\blur0.35" in txt
    assert r"\bord8" in txt
    assert r"\blur0.15" in txt


def test_emphasis_word_gets_style_override(tmp_path: Path) -> None:
    plan = _make_plan(emphasis=["First"])
    out = tmp_path / "out.ass"
    build_ass_karaoke(plan, _WORDS, Settings(), out)
    txt = out.read_text(encoding="utf-8")
    # Emphasis ON tag should appear (mild pop + smooth settle)
    assert r"\fscx122" in txt
    assert r"\fscy122" in txt
    assert r"\blur1.0" in txt
    assert r"\bord9" in txt
    # Reset tag should follow
    assert r"{\r}" in txt


def test_subtitle_position_top_injects_an8(tmp_path: Path) -> None:
    plan = _make_plan(position=SubtitlePosition.top)
    out = tmp_path / "out.ass"
    build_ass_karaoke(plan, _WORDS, Settings(), out)
    txt = out.read_text(encoding="utf-8")
    assert r"{\an8}" in txt


def test_subtitle_position_middle_injects_an5(tmp_path: Path) -> None:
    plan = _make_plan(position=SubtitlePosition.middle)
    out = tmp_path / "out.ass"
    build_ass_karaoke(plan, _WORDS, Settings(), out)
    txt = out.read_text(encoding="utf-8")
    assert r"{\an5}" in txt


def test_subtitle_position_bottom_no_override(tmp_path: Path) -> None:
    plan = _make_plan(position=SubtitlePosition.bottom)
    out = tmp_path / "out.ass"
    build_ass_karaoke(plan, _WORDS, Settings(), out)
    txt = out.read_text(encoding="utf-8")
    # Bottom uses default style alignment — no \an override in first dialogue
    assert r"{\an8}" not in txt
    assert r"{\an5}" not in txt
