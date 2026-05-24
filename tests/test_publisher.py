"""Regression tests for the publisher module (no LLM I/O).

Covers:
  • PublishingPackage validation rules
  • Hashtag/tag normalisation
  • Rule-based fallback generator (always schema-valid)
  • Text formatter output
  • JSON helpers (extract_json, build_correction_message)
  • Pipeline-stage / artifact wiring (publish runs after render and is terminal)
  • Stage handler exposes run_publish

Live LLM clients are NOT exercised here — they are end-to-end.
"""

from __future__ import annotations

import pytest

from shorts_pipeline.jobs.models import ArtifactType, PipelineStage, next_stage
from shorts_pipeline.planner.schema import (
    Beat,
    CameraMotion,
    Clause,
    ColorGrade,
    DecisionLever,
    DecisionLeverType,
    DurationHint,
    EmotionType,
    LutChoice,
    NarrationPlan,
    SubtitlePosition,
    TransitionType,
)
from shorts_pipeline.publisher import (
    CTRStrategy,
    PublishingPackage,
    ThumbnailBrief,
    TitleVariants,
    build_fallback_package,
    render_package_text,
)
from shorts_pipeline.publisher.json_helpers import build_correction_message, extract_json


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _build_plan() -> NarrationPlan:
    """A schema-valid 14-clause NarrationPlan suitable for publisher tests."""
    image_prompt = (
        "Hero close-up portrait of a young Mongol chieftain in his thirties, weathered "
        "face dominant in frame, sharp gaze locked into the lens, fur-lined silk armor, "
        "torchlit dusk light from camera-left, dramatic chiaroscuro, mobile vertical "
        "9:16 composition, photorealistic, ultra-detailed."
    )
    body_prompt = (
        "Wide shot of mounted warriors crossing a frozen river at dawn, low backlit sun, "
        "snow drifting across the bank, cinematic atmosphere, dramatic shadows, "
        "photorealistic, ultra-detailed."
    )

    hook_text = (
        "How does a boy abandoned in the snow at nine become one who rewrote the world? "
        "His name was Temujin."
    )
    body_texts = [
        "Tribes left him when his father was poisoned at a feast.",
        "His mother fed five children on roots and the kindness of strangers.",
        "He stole a horse to ride seven days without sleeping.",
        "He killed his half-brother over a fish and never apologised.",
        "Forty thousand riders swore loyalty to a man with no clan.",
        "He outlawed kidnapping, codified mercy for women, and burned cities.",
        "Persian scholars described him with two words: storm and patience, ceremonial banners cracking.",
        "His armies turned engineers into soldiers and rivers into highways, imperial messengers galloping.",
        "By forty he ruled more land than Rome conquered in eight centuries.",
        "He died on a steppe few men can find on any modern map.",
        "His grave was concealed by a thousand horses driven over the soil.",
        "His name today travels further than his armies ever rode.",
        "An empire he never named outlived the religion he never embraced.",
    ]

    clauses = [
        Clause(
            text=hook_text,
            image_prompt=image_prompt,
            beat=Beat(
                emotion=EmotionType.hook,
                intensity=0.9,
                camera=CameraMotion.ken_burns,
                transition_in=TransitionType.hard_cut,
                duration_hint=DurationHint.short,
                color_grade=ColorGrade.dark_thriller,
                subtitle_position=SubtitlePosition.bottom,
            ),
        )
    ]
    for i, text in enumerate(body_texts):
        clauses.append(
            Clause(
                text=text,
                image_prompt=body_prompt,
                beat=Beat(
                    emotion=EmotionType.tense_buildup if i < 6 else EmotionType.tragic,
                    intensity=0.6,
                    camera=CameraMotion.pan,
                    transition_in=TransitionType.xfade,
                    duration_hint=DurationHint.medium,
                    color_grade=ColorGrade.tragic_cold,
                    subtitle_position=SubtitlePosition.bottom,
                ),
            )
        )

    full_script = " ".join(c.text for c in clauses)
    assert 160 <= len(full_script.split()) <= 210

    return NarrationPlan(
        historical_figure="Genghis Khan",
        cold_open_object="A bone-handled knife wrapped in horsehair",
        decision_lever=DecisionLever(
            lever_type=DecisionLeverType.politics,
            description="A nine-year-old outcast turned grief into a meritocracy of riders.",
            consequence="A nomadic federation grew into the largest contiguous empire in history.",
        ),
        clauses=clauses,
        full_script=full_script,
        lut_choice=LutChoice.tragic_cold,
        end_plate_question="What would YOU have done?",
    )


def _reference_package() -> PublishingPackage:
    return PublishingPackage(
        titles=TitleVariants(
            main="Kareem's Silent Storm Behind 38,387 Points",
            curiosity="The Move No One Could Block - Why?",
            seo="Kareem Abdul-Jabbar Skyhook Story (NBA History)",
        ),
        description=(
            "He barely spoke for thirty years. Then his arm did all the talking.\n\n"
            "How a shy, 7'2 reader turned his height into the most unstoppable shot "
            "the NBA has ever seen.\n\n"
            "What would YOU have done?\n\n"
            "#shorts #history #nba #kareem #skyhook #lakers"
        ),
        hashtags=["#shorts", "#history", "#nba", "#kareem", "#skyhook", "#lakers"],
        tags=[
            "kareem abdul jabbar",
            "skyhook",
            "nba history",
            "lakers",
            "all time scoring record",
            "basketball legends",
            "untold sports history",
            "history shorts",
            "viral history",
            "did you know",
            "story time",
            "biography",
            "sports documentary",
            "athlete biography",
            "basketball facts",
        ],
        thumbnail=ThumbnailBrief(
            concept="Tight 9:16 portrait of a tall basketball player in a #33 Lakers jersey, "
                    "mid-skyhook release, the ball blurred against arena lights.",
            emotion="Awe and disbelief - viewers feel the inevitability of the shot.",
            text_overlay="UNBLOCKABLE",
            image_prompt=(
                "Cinematic close-up portrait of a towering basketball player in Lakers "
                "purple and gold jersey, mid-skyhook release, ball blurred above his "
                "fingertips, arena spotlights as god-rays, vertical 9:16, dramatic "
                "chiaroscuro lighting, photorealistic, ultra-detailed sweat texture."
            ),
        ),
        ctr_strategy=CTRStrategy(
            click_psychology=(
                "Viewers see a recognisable silhouette but the title hints at a hidden "
                "explanation, forcing the click."
            ),
            curiosity_gap=(
                "The title names the move but withholds why it was unguardable."
            ),
            emotional_trigger=(
                "Triumph mixed with quiet defiance - the underdog who wins by being "
                "unteachable."
            ),
            retention_hook=(
                "The script delays the on-court reveal until the climax, then closes "
                "with a question that drives comments."
            ),
        ),
    )


# ── Pipeline wiring ───────────────────────────────────────────────────────────


def test_publish_stage_is_registered_and_terminal() -> None:
    assert PipelineStage.publish.value == "publish"
    assert ArtifactType.publish_package_json.value == "publish_package_json"
    assert next_stage(PipelineStage.i2v) == PipelineStage.render
    assert next_stage(PipelineStage.align) == PipelineStage.i2v
    assert next_stage(PipelineStage.render) == PipelineStage.publish
    assert next_stage(PipelineStage.publish) is None


def test_orchestrator_handler_exposes_run_publish(tmp_path) -> None:
    from shorts_pipeline.config.settings import Settings
    from shorts_pipeline.jobs.store import JobStore
    from shorts_pipeline.orchestrator import orchestrator_stage_handler

    s = Settings(data_dir=tmp_path)
    store = JobStore(tmp_path / "jobs.sqlite")
    handler = orchestrator_stage_handler(s, store)
    for method_name in (
        "run_plan",
        "run_images",
        "run_tts",
        "run_align",
        "run_i2v",
        "run_render",
        "run_publish",
    ):
        method = getattr(handler, method_name, None)
        assert callable(method), f"handler.{method_name} is missing"


# ── Schema ────────────────────────────────────────────────────────────────────


def test_reference_package_validates() -> None:
    pkg = _reference_package()
    assert pkg.titles.main != pkg.titles.curiosity != pkg.titles.seo
    assert len(pkg.tags) >= 12
    assert len(pkg.hashtags) >= 5


def test_duplicate_titles_rejected() -> None:
    with pytest.raises(ValueError, match="three distinct strings"):
        TitleVariants(
            main="Same Title Repeated Three Times",
            curiosity="Same Title Repeated Three Times",
            seo="Same Title Repeated Three Times",
        )


def test_text_overlay_word_limit_enforced() -> None:
    with pytest.raises(ValueError, match="1.5 short words"):
        ThumbnailBrief(
            concept="x" * 50,
            emotion="x" * 20,
            text_overlay="too many short words for one",  # 6 words, 28 chars
            image_prompt="x" * 100,
        )


def test_text_overlay_period_rejected() -> None:
    with pytest.raises(ValueError, match="must not end with a period"):
        ThumbnailBrief(
            concept="x" * 50,
            emotion="x" * 20,
            text_overlay="Too Late.",
            image_prompt="x" * 100,
        )


def test_hashtags_are_normalised_and_deduplicated() -> None:
    pkg = PublishingPackage.model_validate(
        {
            **_reference_package().model_dump(),
            "hashtags": [
                "shorts",          # missing #
                "#History",
                "#history",        # duplicate (case-insensitive)
                "  #nba  ",        # whitespace
                "#viral_history",
                "#untold",
                "#facts",
            ],
        }
    )
    assert pkg.hashtags[0] == "#shorts"
    lowered = [h.lower() for h in pkg.hashtags]
    assert lowered.count("#history") == 1


def test_tags_strip_hash_and_truncate_to_youtube_limit() -> None:
    raw_tags = ["#leading-hash"] + [f"tag-number-{i:02d}" for i in range(1, 35)]
    pkg = PublishingPackage.model_validate(
        {**_reference_package().model_dump(), "tags": raw_tags}
    )
    assert pkg.tags[0] == "leading-hash"
    combined = ",".join(pkg.tags)
    assert len(combined) <= 500


def test_description_auto_appends_hashtags_when_missing() -> None:
    pkg_dict = _reference_package().model_dump()
    pkg_dict["description"] = (
        "He barely spoke for thirty years. Then his arm did all the talking.\n\n"
        "How a shy, 7'2 reader turned his height into the most unstoppable shot "
        "the NBA has ever seen.\n\n"
        "What would YOU have done?"
    )
    pkg = PublishingPackage.model_validate(pkg_dict)
    assert "#shorts" in pkg.description


def test_description_rejects_placeholder_text() -> None:
    pkg_dict = _reference_package().model_dump()
    pkg_dict["description"] = (
        "Lorem ipsum placeholder description for testing purposes that exceeds "
        "the eighty character minimum so we hit the placeholder validator."
    )
    with pytest.raises(ValueError, match="placeholder"):
        PublishingPackage.model_validate(pkg_dict)


# ── Fallback ──────────────────────────────────────────────────────────────────


def test_fallback_produces_schema_valid_package() -> None:
    plan = _build_plan()
    package = build_fallback_package(plan.historical_figure, plan)
    round_tripped = PublishingPackage.model_validate_json(package.model_dump_json())
    assert round_tripped.titles.main
    assert "#shorts" in [h.lower() for h in round_tripped.hashtags]
    assert 12 <= len(round_tripped.tags) <= 35
    assert round_tripped.thumbnail.text_overlay
    assert "Genghis Khan" in round_tripped.titles.main


def test_fallback_handles_unusual_figure_names() -> None:
    plan = _build_plan()
    package = build_fallback_package("J.R.R. Tolkien", plan)
    PublishingPackage.model_validate_json(package.model_dump_json())


# ── Formatter ─────────────────────────────────────────────────────────────────


def test_formatter_renders_all_sections() -> None:
    plan = _build_plan()
    pkg = build_fallback_package(plan.historical_figure, plan)
    text = render_package_text(plan.historical_figure, pkg)
    for marker in (
        "YOUTUBE SHORTS PUBLISHING PACKAGE",
        "[1] TITLE OPTIONS",
        "[2] DESCRIPTION",
        "[3] HASHTAGS",
        "[4] TAGS",
        "[5] THUMBNAIL BRIEF",
        "[6] CTR STRATEGY",
        "END OF PACKAGE",
    ):
        assert marker in text, f"missing section marker: {marker}"
    assert pkg.titles.main in text
    assert pkg.thumbnail.text_overlay in text


# ── JSON helpers ──────────────────────────────────────────────────────────────


def test_extract_json_strips_fences_and_trailing_commas() -> None:
    raw = "Sure! Here is the JSON:\n```json\n{\"a\": 1, \"b\": [1, 2,],}\n```\nDone."
    obj = extract_json(raw)
    assert obj == {"a": 1, "b": [1, 2]}


def test_build_correction_message_targets_failed_field() -> None:
    msg = build_correction_message(
        {"titles": {}}, ValueError("titles must be three distinct strings")
    )
    assert "titles" in msg.lower()
    assert "STRICT OUTPUT CONTRACT" not in msg  # contract lives in system prompt
    assert "complete corrected json" in msg.lower()
