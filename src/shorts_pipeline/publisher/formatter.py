"""Render a PublishingPackage into a clean, paste-ready text block.

This is the file the operator opens in a notepad and copies straight into the
YouTube Studio upload form. The structure follows YouTube Studio field order
(Title → Description → Tags) so they can paste section-by-section.
"""

from __future__ import annotations

from shorts_pipeline.publisher.schema import PublishingPackage

_DIVIDER = "─" * 60


def render_package_text(figure_name: str, package: PublishingPackage) -> str:
    """Return a deterministic UTF-8 string that lays out the entire package."""
    p = package
    parts: list[str] = []

    parts.append(_DIVIDER)
    parts.append(f"YOUTUBE SHORTS PUBLISHING PACKAGE — {figure_name}")
    parts.append(_DIVIDER)
    parts.append("")

    parts.append("[1] TITLE OPTIONS  (paste one into YouTube Studio → Title)")
    parts.append("")
    parts.append(f"  MAIN      : {p.titles.main}")
    parts.append(f"  CURIOSITY : {p.titles.curiosity}")
    parts.append(f"  SEO       : {p.titles.seo}")
    parts.append("")

    parts.append("[2] DESCRIPTION  (paste into YouTube Studio → Description)")
    parts.append("")
    parts.extend(p.description.splitlines() or [""])
    parts.append("")

    parts.append("[3] HASHTAGS  (already inside the description; copy if needed)")
    parts.append("")
    parts.append("  " + " ".join(p.hashtags))
    parts.append("")

    parts.append("[4] TAGS  (paste into YouTube Studio → Tags, comma-separated)")
    parts.append("")
    parts.append("  " + ", ".join(p.tags))
    parts.append("")

    parts.append("[5] THUMBNAIL BRIEF")
    parts.append("")
    parts.append(f"  Concept       : {p.thumbnail.concept}")
    parts.append(f"  Emotion       : {p.thumbnail.emotion}")
    parts.append(f"  Text overlay  : {p.thumbnail.text_overlay}")
    parts.append("")
    parts.append("  Image prompt  :")
    for line in p.thumbnail.image_prompt.splitlines() or [p.thumbnail.image_prompt]:
        parts.append(f"    {line}")
    parts.append("")

    parts.append("[6] CTR STRATEGY")
    parts.append("")
    parts.append(f"  Click psychology  : {p.ctr_strategy.click_psychology}")
    parts.append(f"  Curiosity gap     : {p.ctr_strategy.curiosity_gap}")
    parts.append(f"  Emotional trigger : {p.ctr_strategy.emotional_trigger}")
    parts.append(f"  Retention hook    : {p.ctr_strategy.retention_hook}")
    parts.append("")

    parts.append(_DIVIDER)
    parts.append("END OF PACKAGE — ready to upload.")
    parts.append(_DIVIDER)
    parts.append("")

    return "\n".join(parts)
