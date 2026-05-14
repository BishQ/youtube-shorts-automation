"""Pydantic schema for a YouTube Shorts publishing package.

A PublishingPackage is the COPY-PASTE-READY metadata bundle generated for every
finished video: titles, description, hashtags, tags, thumbnail brief, and CTR
strategy. Validation is intentionally strict so that downstream consumers
(creator dashboard, upload automation) never receive half-formed packages.
"""

from __future__ import annotations

import re
from pydantic import BaseModel, Field, field_validator, model_validator

# ── Hard limits (YouTube + Shorts platform realities) ────────────────────────

# YouTube hard limit on titles is 100 characters; Shorts CTR collapses past ~60.
TITLE_MIN_CHARS = 8
TITLE_MAX_CHARS = 95

# YouTube description hard limit is 5000 chars; Shorts UI clips after ~150.
DESCRIPTION_MIN_CHARS = 80
DESCRIPTION_MAX_CHARS = 3500

# YouTube combined-tag character limit is 500. We keep tags short to stay
# safely below it while still giving the algorithm a useful surface area.
TAGS_MIN_COUNT = 12
TAGS_MAX_COUNT = 35
TAG_MIN_LEN = 2
TAG_MAX_LEN = 60

HASHTAGS_MIN_COUNT = 5
HASHTAGS_MAX_COUNT = 15
HASHTAG_MIN_LEN = 2
HASHTAG_MAX_LEN = 40

# Thumbnail text overlay must read on a phone in <0.4 seconds.
OVERLAY_MIN_CHARS = 1
OVERLAY_MAX_CHARS = 32
OVERLAY_MAX_WORDS = 5

THUMBNAIL_PROMPT_MIN_CHARS = 80
THUMBNAIL_CONCEPT_MIN_CHARS = 30
THUMBNAIL_EMOTION_MIN_CHARS = 10
CTR_FIELD_MIN_CHARS = 20

_HASHTAG_RE = re.compile(r"^#[A-Za-z0-9_]{1,40}$")
_TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .'\-_/&+]{0,59}$")
_NON_PRINT_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _clean_line(value: str) -> str:
    return _NON_PRINT_RE.sub("", value).strip()


def _word_count(value: str) -> int:
    return len([w for w in re.split(r"\s+", value.strip()) if w])


# ── Sub-models ───────────────────────────────────────────────────────────────


class TitleVariants(BaseModel):
    """Three psychologically distinct title rolls for A/B/C selection."""

    main: str = Field(..., min_length=TITLE_MIN_CHARS, max_length=TITLE_MAX_CHARS)
    curiosity: str = Field(..., min_length=TITLE_MIN_CHARS, max_length=TITLE_MAX_CHARS)
    seo: str = Field(..., min_length=TITLE_MIN_CHARS, max_length=TITLE_MAX_CHARS)

    @field_validator("main", "curiosity", "seo", mode="before")
    @classmethod
    def _strip(cls, v: object) -> object:
        if isinstance(v, str):
            return _clean_line(v)
        return v

    @model_validator(mode="after")
    def _no_duplicate_variants(self) -> TitleVariants:
        seen = {self.main.lower(), self.curiosity.lower(), self.seo.lower()}
        if len(seen) < 3:
            raise ValueError(
                "title variants must be three distinct strings — duplicates kill "
                "the A/B test signal. Rewrite each title from a different angle: "
                "main = balanced clickable, curiosity = mystery-heavy, seo = searchable."
            )
        return self


class ThumbnailBrief(BaseModel):
    """Production-ready brief for the thumbnail artist or image-gen pipeline."""

    concept: str = Field(..., min_length=THUMBNAIL_CONCEPT_MIN_CHARS, max_length=600)
    emotion: str = Field(..., min_length=THUMBNAIL_EMOTION_MIN_CHARS, max_length=300)
    text_overlay: str = Field(..., min_length=OVERLAY_MIN_CHARS, max_length=OVERLAY_MAX_CHARS)
    image_prompt: str = Field(..., min_length=THUMBNAIL_PROMPT_MIN_CHARS, max_length=2000)

    @field_validator("concept", "emotion", "text_overlay", "image_prompt", mode="before")
    @classmethod
    def _strip(cls, v: object) -> object:
        if isinstance(v, str):
            return _clean_line(v)
        return v

    @field_validator("text_overlay")
    @classmethod
    def _validate_overlay(cls, v: str) -> str:
        wc = _word_count(v)
        if wc < 1 or wc > OVERLAY_MAX_WORDS:
            raise ValueError(
                f"thumbnail.text_overlay must be 1–{OVERLAY_MAX_WORDS} short words "
                f"(got {wc} words: {v!r}). The overlay must be readable on a phone "
                "in under half a second — examples: 'His Last Mistake', 'Forbidden Truth', "
                "'They Betrayed Him', 'Too Late'."
            )
        if v.endswith("."):
            raise ValueError(
                "thumbnail.text_overlay must not end with a period — overlays are punchy "
                "fragments, not sentences."
            )
        return v


class CTRStrategy(BaseModel):
    """Why-it-clicks analysis. Each field is a complete rationale, not bullets."""

    click_psychology: str = Field(..., min_length=CTR_FIELD_MIN_CHARS, max_length=800)
    curiosity_gap: str = Field(..., min_length=CTR_FIELD_MIN_CHARS, max_length=800)
    emotional_trigger: str = Field(..., min_length=CTR_FIELD_MIN_CHARS, max_length=800)
    retention_hook: str = Field(..., min_length=CTR_FIELD_MIN_CHARS, max_length=800)

    @field_validator(
        "click_psychology", "curiosity_gap", "emotional_trigger", "retention_hook",
        mode="before",
    )
    @classmethod
    def _strip(cls, v: object) -> object:
        if isinstance(v, str):
            return _clean_line(v)
        return v


# ── Top-level package ────────────────────────────────────────────────────────


class PublishingPackage(BaseModel):
    """Complete, copy-paste-ready YouTube Shorts publishing bundle."""

    titles: TitleVariants
    description: str = Field(..., min_length=DESCRIPTION_MIN_CHARS, max_length=DESCRIPTION_MAX_CHARS)
    hashtags: list[str] = Field(..., min_length=HASHTAGS_MIN_COUNT, max_length=HASHTAGS_MAX_COUNT)
    tags: list[str] = Field(..., min_length=TAGS_MIN_COUNT, max_length=TAGS_MAX_COUNT)
    thumbnail: ThumbnailBrief
    ctr_strategy: CTRStrategy

    @field_validator("description", mode="before")
    @classmethod
    def _strip_description(cls, v: object) -> object:
        if isinstance(v, str):
            return _NON_PRINT_RE.sub("", v).strip()
        return v

    @field_validator("hashtags", mode="before")
    @classmethod
    def _normalize_hashtags(cls, v: object) -> object:
        if not isinstance(v, list):
            return v
        out: list[str] = []
        seen: set[str] = set()
        for item in v:
            if not isinstance(item, str):
                continue
            tag = _clean_line(item)
            if not tag:
                continue
            if not tag.startswith("#"):
                tag = "#" + tag.lstrip("#")
            tag = re.sub(r"\s+", "", tag)
            key = tag.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(tag)
        return out

    @field_validator("hashtags")
    @classmethod
    def _validate_hashtags(cls, v: list[str]) -> list[str]:
        invalid = [t for t in v if not _HASHTAG_RE.match(t)]
        if invalid:
            raise ValueError(
                f"hashtags must match #[A-Za-z0-9_]{{1,40}} — invalid: {invalid[:5]}. "
                "Use single-token hashtags only (no spaces, no punctuation, no emoji)."
            )
        return v

    @field_validator("tags", mode="before")
    @classmethod
    def _normalize_tags(cls, v: object) -> object:
        if not isinstance(v, list):
            return v
        out: list[str] = []
        seen: set[str] = set()
        total_chars = 0
        for item in v:
            if not isinstance(item, str):
                continue
            tag = _clean_line(item).lstrip("#").strip()
            if not tag:
                continue
            tag = re.sub(r"\s+", " ", tag)
            key = tag.lower()
            if key in seen:
                continue
            # YouTube hard cap: combined tag chars (incl. commas) ≤ 500.
            projected = total_chars + len(tag) + (1 if total_chars else 0)
            if projected > 480:
                break
            seen.add(key)
            out.append(tag)
            total_chars = projected
        return out

    @field_validator("tags")
    @classmethod
    def _validate_tags(cls, v: list[str]) -> list[str]:
        invalid = [t for t in v if not (TAG_MIN_LEN <= len(t) <= TAG_MAX_LEN and _TAG_RE.match(t))]
        if invalid:
            raise ValueError(
                f"tags must be {TAG_MIN_LEN}–{TAG_MAX_LEN} chars, ASCII letters/numbers/spaces "
                f"and basic punctuation only (no '#', no emoji). Invalid: {invalid[:5]}."
            )
        return v

    @model_validator(mode="after")
    def _enforce_quality_floor(self) -> PublishingPackage:
        # Description should reference at least one hashtag from the hashtag list
        # OR contain its own — otherwise discoverability is wasted.
        desc_lower = self.description.lower()
        if "#" not in self.description:
            joined = self.description.rstrip()
            self.description = (
                joined + ("\n\n" if not joined.endswith("\n") else "") + " ".join(self.hashtags)
            )
        # Block low-effort placeholder content
        bad_markers = ("lorem ipsum", "todo", "tbd", "[insert", "n/a")
        for marker in bad_markers:
            if marker in desc_lower:
                raise ValueError(
                    f"description contains placeholder text {marker!r}. "
                    "The publishing package must be fully production-ready."
                )
        return self
