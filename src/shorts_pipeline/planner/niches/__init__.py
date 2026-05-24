"""Niche registry for the planner.

Each niche module exposes:
    SYSTEM_PROMPT : str
    user_prompt(topic: str) -> str

Call ``get_niche(name)`` to fetch a niche by its canonical slug. The slug is
also the module filename — e.g. ``business``, ``crime``, ``military``.

Usage in the planner client:

    from shorts_pipeline.planner.niches import get_niche
    niche = get_niche("military")
    messages = [
        {"role": "system", "content": niche.SYSTEM_PROMPT},
        {"role": "user",   "content": niche.user_prompt(topic)},
    ]
"""
from types import ModuleType
from typing import Dict, List

from . import (
    business,
    cosmic,
    crime,
    cults,
    edutainment,
    health,
    history,
    lost_tech,
    military,
    mythology,
    psychology,
    science,
    sports,
    survival,
    tech_hackers,
    wealth,
)


# Canonical slug → (display label, module). Order is the recommended display order
# in the UI — original 6 first, then the 10 new additions ranked by retention strength.
_NICHES: Dict[str, tuple[str, ModuleType]] = {
    # Original 6
    "business":     ("Business, Geopolitics & Power",   business),
    "science":      ("Science, Space & Future",         science),
    "crime":        ("Crime, Mysteries & Dark World",   crime),
    "history":      ("History, Civilizations & Lore",   history),
    "psychology":   ("Psychology, Brain & Philosophy",  psychology),
    "edutainment":  ("Edutainment",                     edutainment),
    # 10 new niches
    "military":     ("Military, War & Espionage",       military),
    "sports":       ("Sports Legends & Sporting Drama", sports),
    "survival":     ("Survival, Exploration & Disasters", survival),
    "mythology":    ("Mythology, Gods & Sacred Stories", mythology),
    "tech_hackers": ("Tech, Hackers & The Internet",    tech_hackers),
    "health":       ("Health, Body & Longevity",        health),
    "wealth":       ("Wealth, Dynasties & Hidden Power", wealth),
    "cosmic":       ("Cosmic Horror / Deep Time",        cosmic),
    "cults":        ("Cults & Belief Systems",           cults),
    "lost_tech":    ("Forgotten Inventions & Lost Tech", lost_tech),
}


class UnknownNicheError(KeyError):
    """Raised when ``get_niche`` is called with a slug that isn't registered."""


def list_niches() -> List[tuple[str, str]]:
    """Return [(slug, display_label), ...] in display order."""
    return [(slug, label) for slug, (label, _mod) in _NICHES.items()]


def get_niche(name: str) -> ModuleType:
    """Resolve a niche by slug. Case-insensitive. Strips whitespace."""
    key = (name or "").strip().lower()
    if key not in _NICHES:
        raise UnknownNicheError(
            f"unknown niche {name!r}; available: {sorted(_NICHES)}"
        )
    return _NICHES[key][1]


def niche_label(name: str) -> str:
    """Return the human display label for a niche slug."""
    key = (name or "").strip().lower()
    if key not in _NICHES:
        raise UnknownNicheError(
            f"unknown niche {name!r}; available: {sorted(_NICHES)}"
        )
    return _NICHES[key][0]


__all__ = [
    "UnknownNicheError",
    "get_niche",
    "list_niches",
    "niche_label",
]
