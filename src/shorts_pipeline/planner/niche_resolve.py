"""Resolve planner niche slugs to compact-prompt keys."""

from __future__ import annotations

from shorts_pipeline.planner.niches_compact import COMPACT_DATA

# Legacy job/API values → compact niche key.
_ALIASES: dict[str, str] = {
    "historical_figure": "documentary",
    "general": "documentary",
    "facts": "edutainment",  # facts was removed from compact; closest voice
}

NICHE_LABELS: dict[str, str] = {
    "business": "Business",
    "cosmic": "Cosmic",
    "crime": "Crime",
    "cults": "Cults",
    "documentary": "Documentary",
    "edutainment": "Edutainment",
    "health": "Health",
    "history": "History",
    "lost_tech": "Lost Tech",
    "military": "Military",
    "mythology": "Mythology",
    "psychology": "Psychology",
    "science": "Science",
    "sports": "Sports",
    "survival": "Survival",
    "tech_hackers": "Tech / Hackers",
    "wealth": "Wealth",
}


def available_niches() -> list[str]:
    return sorted(COMPACT_DATA.keys())


def resolve_niche(value: str | None) -> str:
    """Map API/DB value to a compact-prompt niche key."""
    raw = (value or "documentary").strip().lower()
    raw = _ALIASES.get(raw, raw)
    if raw in COMPACT_DATA:
        return raw
    return "documentary"


def is_valid_niche(value: str | None) -> bool:
    raw = (value or "").strip().lower()
    raw = _ALIASES.get(raw, raw)
    return raw in COMPACT_DATA


def niche_label(niche: str) -> str:
    return NICHE_LABELS.get(niche, niche.replace("_", " ").title())
