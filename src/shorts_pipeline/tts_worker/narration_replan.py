"""Re-plan narration when raw TTS exceeds the duration threshold.

Policy:
* Raw TTS ≤ 65s → mild atempo speedup (≤1.15x) to land near 59s — no re-plan.
* Raw TTS > 65s → re-write the plan; feedback states exactly how many seconds
  over the 65s limit (e.g. 69s → "4 seconds over the 65s limit").
"""

from __future__ import annotations

from shorts_pipeline.config.settings import Settings
from shorts_pipeline.planner.schema import NarrationPlan
from shorts_pipeline.planner.niche_caps import caps_for


def narration_replan_threshold_s(settings: Settings) -> float:
    return settings.narration_replan_threshold_s


def build_narration_too_long_feedback(
    plan: NarrationPlan,
    *,
    measured_s: float,
    settings: Settings,
    attempt: int,
) -> str:
    niche = (getattr(plan, "niche", None) or "").strip().lower() or None
    _min_w, max_w, _max_syl = caps_for(niche)
    word_count = len(plan.full_script.split())
    target = settings.narration_fit_target_s
    limit = narration_replan_threshold_s(settings)
    over_limit = max(0.0, measured_s - limit)
    shorten_s = max(0.0, measured_s - target)
    words_to_cut = max(1, round(shorten_s * word_count / measured_s)) if measured_s > 0 else 1
    return (
        f"PRODUCER NOTE (attempt {attempt}): This narration is "
        f"{over_limit:.1f} seconds over the {limit:.0f}s limit "
        f"(measured {measured_s:.1f}s).\n\n"
        f"Rewrite the FULL plan JSON with SHORTER narration while keeping exactly "
        f"{len(plan.clauses)} clauses.\n"
        f"• Final TTS must land near {target:.0f}s — remove ~{words_to_cut} words "
        f"({shorten_s:.1f}s of spoken time).\n"
        f"• Current word count: {word_count} (niche max ~{max_w} words).\n"
        f"• Cut filler, tighten sentences, remove redundant phrases.\n"
        f"• Keep the hook, story arc, image prompts, beats, and end-plate question.\n"
        f"• Do NOT rely on faster delivery — write fewer words."
    )
