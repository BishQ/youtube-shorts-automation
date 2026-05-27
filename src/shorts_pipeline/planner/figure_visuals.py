"""Visual appearance anchors for image prompts (no real names in Comfy text)."""

from __future__ import annotations

import re

# Keys: lowercase figure slug; value: prepend when figure_present and prompt lacks region cues.
_FIGURE_ANCHORS: dict[str, str] = {
    "genghis khan": (
        "a powerfully built Mongol man in his forties, East Asian Mongolian bone structure, "
        "weathered tan skin, narrow almond eyes, high cheekbones, black hair in traditional "
        "Mongol topknot or fur-trimmed helm, wolf-grey layered leather lamellar armour, "
    ),
}

_REGION_CUES = re.compile(
    r"\b(mongol|mongolian|steppe|east asian|central asian|almond eyes|lamellar|deel|"
    r"warhorse|genghis|khan|nomadic|yurt)\b",
    re.IGNORECASE,
)


def enrich_image_prompt(
    prompt: str,
    figure_name: str,
    *,
    figure_present: bool,
) -> str:
    """Prepend ethnicity/era anchors when the LLM omitted them (fixes generic Western faces)."""
    if not figure_present or not prompt.strip():
        return prompt
    if _REGION_CUES.search(prompt):
        return prompt
    key = figure_name.strip().lower()
    anchor = _FIGURE_ANCHORS.get(key)
    if not anchor:
        return prompt
    return anchor + prompt.strip()
