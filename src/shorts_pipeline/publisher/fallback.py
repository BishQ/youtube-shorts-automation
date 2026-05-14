"""Rule-based fallback PublishingPackage generator.

Used as the absolute last-resort safety net when every LLM tier fails. The
goal is never "great" packaging — that's what the LLM is for — but to ensure
that no completed video is ever blocked from upload because the metadata
generator was unreachable.

Inputs come exclusively from the already-validated NarrationPlan plus the
figure name; this function performs no I/O and never raises.
"""

from __future__ import annotations

import re

from shorts_pipeline.planner.schema import NarrationPlan
from shorts_pipeline.publisher.schema import (
    CTRStrategy,
    PublishingPackage,
    ThumbnailBrief,
    TitleVariants,
)

# ── Generic, evergreen tag/hashtag pools (safe for any historical-figure short) ──

_BASE_TAGS: tuple[str, ...] = (
    "history",
    "history shorts",
    "historical figures",
    "history facts",
    "biography",
    "true history",
    "untold history",
    "ancient history",
    "world history",
    "historical mystery",
    "shocking history",
    "viral history",
    "shorts",
    "short documentary",
    "story time",
    "did you know",
)

_BASE_HASHTAGS: tuple[str, ...] = (
    "#shorts",
    "#history",
    "#historyshorts",
    "#historicalfigures",
    "#viralhistory",
    "#untoldhistory",
    "#storytime",
    "#shockingtruth",
    "#fyp",
)

_NAME_SLUG_RE = re.compile(r"[^A-Za-z0-9]+")
_WORD_RE = re.compile(r"[A-Za-z0-9]+")


def _slug_word(name: str) -> str:
    return _NAME_SLUG_RE.sub("", name).lower()


def _name_words(name: str) -> list[str]:
    return [w for w in _WORD_RE.findall(name) if len(w) >= 2]


def _truncate(text: str, max_chars: int) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    cut = text[: max_chars - 1].rstrip(" ,.;:—-")
    return cut + "…"


def _first_sentence(text: str) -> str:
    parts = re.split(r"(?<=[.?!])\s+", text.strip(), maxsplit=1)
    return parts[0].strip() if parts else text.strip()


def build_fallback_package(figure_name: str, plan: NarrationPlan) -> PublishingPackage:
    """Return a schema-valid PublishingPackage built deterministically from the plan."""

    figure = figure_name.strip() or plan.historical_figure
    figure_title = figure.title()
    name_slug = _slug_word(figure)
    name_words = _name_words(figure)

    hook_clause = plan.clauses[0].text if plan.clauses else figure
    hook_sentence = _first_sentence(hook_clause)
    second_sentence = (
        _first_sentence(plan.clauses[1].text)
        if len(plan.clauses) >= 2
        else hook_sentence
    )

    main_title = _truncate(f"{figure_title}: {hook_sentence}", 90)
    curiosity_title = _truncate(
        f"The Hidden Truth About {figure_title}", 90
    )
    seo_title = _truncate(
        f"{figure_title} — The Untold Story (Real History)", 90
    )

    description_lines = [
        f"{hook_sentence}",
        "",
        f"{second_sentence} {plan.decision_lever.consequence.strip()}",
        "",
        f"{plan.end_plate_question}",
        "",
        " ".join(_BASE_HASHTAGS),
    ]
    description = _truncate("\n".join(description_lines), 1800)

    hashtags: list[str] = []
    seen_hashtags: set[str] = set()
    if name_slug and 2 <= len(name_slug) <= 40:
        primary = f"#{name_slug}"
        hashtags.append(primary)
        seen_hashtags.add(primary.lower())
    for tag in _BASE_HASHTAGS:
        if tag.lower() in seen_hashtags:
            continue
        hashtags.append(tag)
        seen_hashtags.add(tag.lower())

    tags: list[str] = []
    seen_tags: set[str] = set()

    def _push_tag(value: str) -> None:
        clean = re.sub(r"\s+", " ", value).strip().lstrip("#")
        if not clean or len(clean) > 60 or len(clean) < 2:
            return
        key = clean.lower()
        if key in seen_tags:
            return
        seen_tags.add(key)
        tags.append(clean)

    _push_tag(figure_title)
    for w in name_words:
        _push_tag(w)
    _push_tag(f"{figure_title} biography")
    _push_tag(f"{figure_title} history")
    _push_tag(f"who was {figure_title}")
    _push_tag(f"{figure_title} story")
    _push_tag(f"{figure_title} facts")
    for t in _BASE_TAGS:
        _push_tag(t)

    # Schema requires 12+ tags; pad with safe evergreen long-tails if needed.
    safety_pool = (
        "real history facts",
        "history documentary",
        "historical events",
        "history explained",
        "history channel",
        "famous people in history",
        "people who changed history",
        "history retold",
        "story of",
    )
    for extra in safety_pool:
        if len(tags) >= 22:
            break
        _push_tag(extra)

    overlay_word_pool = ("HIS LAST CHOICE", "TOO LATE", "FORBIDDEN TRUTH", "THE REAL STORY")
    overlay = overlay_word_pool[hash(name_slug or figure) % len(overlay_word_pool)]

    thumbnail = ThumbnailBrief(
        concept=(
            f"Vertical 9:16 hero portrait of {figure_title} centred frame, "
            f"intense direct gaze, era-accurate clothing, the {plan.cold_open_object} "
            "visible in the lower foreground as a secondary symbol."
        ),
        emotion=(
            "Shock and unresolved tension — the viewer must feel that something "
            "irreversible has just happened to this person."
        ),
        text_overlay=overlay,
        image_prompt=(
            f"Cinematic close-up portrait of {figure_title}, era-accurate clothing, "
            "single dominant subject, dramatic chiaroscuro lighting from the side, "
            f"shallow depth of field, the {plan.cold_open_object} blurred in the lower "
            "foreground, mobile-vertical 9:16 composition, ultra-detailed skin texture, "
            "high contrast, moody color grade, eyes locked toward camera, no text, "
            "no watermark, photorealistic."
        ),
    )

    ctr = CTRStrategy(
        click_psychology=(
            f"The thumbnail promises a face most viewers half-recognise but cannot place, "
            f"and the title hints at a secret the viewer assumed they already knew about "
            f"{figure_title}. That gap between assumed knowledge and promised revelation "
            "is what forces the click."
        ),
        curiosity_gap=(
            f"The hook sentence — \"{_truncate(hook_sentence, 140)}\" — names a moment "
            "but withholds the cause. The viewer must watch to learn what triggered it."
        ),
        emotional_trigger=(
            "The decision lever inside the script — "
            f"{_truncate(plan.decision_lever.description, 160)} — taps a primal "
            "moral question, which is what drives shares and comments."
        ),
        retention_hook=(
            "The narration delays the consequence until the final third, then closes with "
            f"the question \"{plan.end_plate_question}\" so re-watch and reply behaviour spike."
        ),
    )

    return PublishingPackage(
        titles=TitleVariants(main=main_title, curiosity=curiosity_title, seo=seo_title),
        description=description,
        hashtags=hashtags,
        tags=tags,
        thumbnail=thumbnail,
        ctr_strategy=ctr,
    )
