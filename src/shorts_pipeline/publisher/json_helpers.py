"""Shared JSON-extraction and correction helpers for publisher LLM calls.

We deliberately reuse `_extract_json` from the planner module: the same JSON
sanitisation logic (strip <think> tags, repair trailing commas, peel surrounding
prose) is needed verbatim. Keeping a single implementation prevents drift.
"""

from __future__ import annotations

from typing import Any

from shorts_pipeline.planner.client import _extract_json as _planner_extract_json

extract_json = _planner_extract_json


def build_correction_message(obj: dict[str, Any], err: Exception) -> str:
    """
    Build a focused correction message after a publisher schema validation
    failure. We return only the actionable diff — no apologies, no preamble.
    """
    err_str = str(err)
    keys_seen = sorted(obj.keys()) if isinstance(obj, dict) else []

    hints: list[str] = []

    if "titles" in err_str:
        hints.append(
            "• titles: provide three DIFFERENT strings under titles.main / titles.curiosity / "
            "titles.seo. Each 8–95 chars, no surrounding quotes, no trailing period, no emoji. "
            "main = balanced clickable, curiosity = mystery-heavy, seo = searchable."
        )
    if "description" in err_str:
        hints.append(
            "• description: 80–3500 chars. 2–4 short paragraphs separated by blank lines. "
            "End with the hashtags inline. No 'lorem' / 'todo' / 'tbd' / '[insert]' / 'n/a'."
        )
    if "hashtags" in err_str:
        hints.append(
            "• hashtags: 5–15 items. Every item starts with '#' followed by 1–40 alphanumerics "
            "or underscores — no spaces, no emoji, no punctuation. Always include #shorts."
        )
    if "tags" in err_str:
        hints.append(
            "• tags: 15–30 items. Each tag 2–60 ASCII chars, NO leading '#', no emoji. "
            "Mix figure name, era, niche keywords, broad keywords, long-tail phrases."
        )
    if "text_overlay" in err_str:
        hints.append(
            "• thumbnail.text_overlay: 1–5 short words, 1–32 chars, no period at end. "
            "Examples: 'HIS LAST MISTAKE', 'Forbidden Truth', 'Too Late'."
        )
    if "image_prompt" in err_str:
        hints.append(
            "• thumbnail.image_prompt: ≥80 chars. Cinematic prompt with shot type, lighting, "
            "single dominant subject, mobile-vertical (9:16) composition, dramatic atmosphere."
        )
    if "ctr_strategy" in err_str or "click_psychology" in err_str:
        hints.append(
            "• ctr_strategy: each of click_psychology / curiosity_gap / emotional_trigger / "
            "retention_hook must be ≥20 chars, written as a full sentence explaining the lever."
        )

    if not hints:
        hints.append(
            "• Re-read the STRICT OUTPUT CONTRACT in the system prompt and satisfy every "
            "constraint exactly."
        )

    return (
        "Your JSON failed validation. Fix every error listed below:\n"
        f"{err_str}\n\n"
        "Targeted fixes:\n"
        + "\n".join(hints)
        + "\n\n"
        f"Top-level keys you returned: {keys_seen}\n"
        "Required top-level keys: ['ctr_strategy', 'description', 'hashtags', 'tags', "
        "'thumbnail', 'titles']\n\n"
        "Return the COMPLETE corrected JSON object only — no prose, no markdown fences, "
        "no trailing commas, no truncation."
    )
