"""Architecture MAIN — production pipeline.

Built from lessons learned across the 5-architecture bench:
  - D_critique (draft → rubric → rewrite) was the best (3/17 success).
  - C_curiosity (14-rung ladder) hit edutainment niche.
  - All others kept under-writing or over-questioning.

This architecture combines what worked:
  Stage 1 — DRAFT  (low-constraint generation, get story on page)
  Stage 2 — RUBRIC (Python computes issues — word count, '?', identity, banned)
  Stage 3 — REWRITE (LLM gets draft + issues + ladder hints)
  Stage 4 — REPAIR (final ±7 word-count check, surgical fix only)
  Stage 5 — VISUALS (14 image+motion+beat blocks)

Hard rules left at this stage (everything else softened or removed):
  - Word range with ±7 tolerance
  - ZERO '?' in clauses 2-14 (only clause 1 hook + END_QUESTION)
  - Banned image content (skyscraper / smartphone / laptop / etc.)
  - Shot type required (lighting validator already disabled)
  - 14 clauses exact

Designed for DeepSeek-chat but works on any OpenAI-compatible endpoint.
"""

from __future__ import annotations

import time

from shorts_pipeline.config.settings import Settings
from shorts_pipeline.planner.multistage import parse_stage_a
from shorts_pipeline.planner.niche_caps import caps_for
from shorts_pipeline.planner.niches_compact import COMPACT_DATA

from ._shared import (
    apply_latinate_swaps,
    cadence_rewrite,
    find_narrative_issues,
    llm_post,
    merge_and_validate,
    repair_narrative,
    strip_ai_phrases,
    strip_stray_questions,
    visual_stage,
)


# ── Stage 1 — Draft (low constraint) ─────────────────────────────────────────

def _stage1_draft(settings: Settings, niche: str, topic: str, timeout_s: float) -> dict:
    """Get a usable draft on the page. Don't worry about perfection yet."""
    min_w, max_w, _ = caps_for(niche)
    d = COMPACT_DATA[niche]
    sys_p = f"""You write {d['voice']}.

Topic: {topic!r}
Niche scope: {d['scope']}.
Transformation: {d['transformation']}.

Write a 14-clause narration. Aim for {min_w}-{max_w} words total.

CURIOSITY LADDER (loose guide):
  - Clause 1: question — role + impossible situation, no name (under 14 words)
  - Clause 2: deepen mystery, still no name
  - Clause 3 or 4: identity reveal (the name appears here)
  - Clauses 5-11: stakes, decision, cost, consequence, contradiction
  - Clauses 12-14: macro reframe, end with the setup for END_QUESTION

OUTPUT FORMAT:

HISTORICAL_FIGURE: <name>
COLD_OPEN_OBJECT: <one physical object, no person>
LEVER_TYPE: law | geography | politics
LEVER_DESC: <one sentence>
LEVER_CONSEQ: <one sentence>
LUT: epic_warm | tragic_cold | ancient_sepia | dark_thriller | golden_hour
END_QUESTION: <one rhetorical question ending in '?'>

CLAUSE 1: <hook question>
CLAUSE 2: <text>
...
CLAUSE 14: <text>"""
    user_p = f"Draft a 14-clause narration for {topic!r}."
    raw = llm_post(settings, system=sys_p, user=user_p,
                   max_tokens=1500, timeout_s=timeout_s, temperature=0.55)
    parsed = parse_stage_a(raw)
    if len([c for c in parsed["clauses"] if c["text"]]) < 14:
        n = len([c for c in parsed["clauses"] if c["text"]])
        e = RuntimeError(f"stage1 draft: only {n}/14 clauses parsed")
        raise e
    return parsed


# ── Stage 2 — Rubric (Python) ────────────────────────────────────────────────

def _stage2_rubric(narrative: dict, niche: str) -> list[str]:
    """Compute mechanical issues for the rewrite stage. Pure Python."""
    min_w, max_w, _ = caps_for(niche)
    # Note: validator uses ±7 tolerance, so we ask for fixes only if outside that.
    TOL = 7
    issues = find_narrative_issues(narrative, min_w - TOL + 1, max_w + TOL - 1)
    return issues


# ── Stage 3 — Rewrite (only if rubric had issues) ────────────────────────────

def _stage3_rewrite(
    settings: Settings, niche: str, topic: str, draft: dict,
    issues: list[str], timeout_s: float,
) -> dict:
    """LLM gets draft + issues, rewrites in one focused pass."""
    if not issues:
        return draft
    return repair_narrative(
        settings, narrative=draft, issues=issues, niche=niche, timeout_s=timeout_s,
    )


# ── Stage 4 — Final repair check ─────────────────────────────────────────────

def _stage4_final_repair(
    settings: Settings, niche: str, topic: str, narrative: dict, timeout_s: float,
) -> dict:
    """One more rubric+repair pass if word count is still off."""
    issues = _stage2_rubric(narrative, niche)
    if not issues:
        return narrative
    return repair_narrative(
        settings, narrative=narrative, issues=issues, niche=niche, timeout_s=timeout_s,
    )


# ── Main driver ──────────────────────────────────────────────────────────────

def generate(
    settings: Settings,
    *,
    niche: str,
    topic: str,
    timeout_s: float = 900.0,
    previous_error: str | None = None,
) -> tuple[dict, dict]:
    debug: dict = {"arch": "MAIN", "stages": {}}
    if previous_error:
        debug["feedback_received"] = previous_error[:300]

    # 1) Draft
    t = time.time()
    draft = _stage1_draft(settings, niche, topic, timeout_s)
    debug["stages"]["1_draft"] = {
        "time_s": round(time.time() - t, 1),
        "words": len(draft["full_script"].split()),
    }

    # 2) Rubric
    issues = _stage2_rubric(draft, niche)
    debug["stages"]["2_rubric"] = {"issues_found": len(issues), "issues": issues[:3]}

    # 3) Rewrite (only if issues)
    narrative = draft
    if issues:
        t = time.time()
        narrative = _stage3_rewrite(settings, niche, topic, draft, issues, timeout_s)
        debug["stages"]["3_rewrite"] = {
            "time_s": round(time.time() - t, 1),
            "words": len(narrative["full_script"].split()),
        }
    else:
        debug["stages"]["3_rewrite"] = {"skipped": True}

    # Python cleanup
    for i, c in enumerate(narrative["clauses"]):
        text = strip_ai_phrases(apply_latinate_swaps(c["text"])).strip()
        narrative["clauses"][i]["text"] = text
    # Strip stray '?' from clauses 2-14 (and 2nd '?' from clause 1)
    narrative = strip_stray_questions(narrative)
    narrative["full_script"] = " ".join(c["text"] for c in narrative["clauses"]).strip()

    # 3.5) Cadence rewrite — break uniform sentence pacing if needed
    t = time.time()
    narrative_pre_cadence = narrative
    narrative = cadence_rewrite(settings, narrative=narrative, niche=niche, timeout_s=timeout_s)
    rewrote = narrative is not narrative_pre_cadence
    debug["stages"]["3_5_cadence"] = {
        "time_s": round(time.time() - t, 1),
        "rewrote": rewrote,
        "words_after": len(narrative["full_script"].split()),
    }

    # 4) Final repair (one more shot if still off)
    t = time.time()
    narrative = _stage4_final_repair(settings, niche, topic, narrative, timeout_s)
    debug["stages"]["4_final_repair"] = {
        "time_s": round(time.time() - t, 1),
        "words": len(narrative["full_script"].split()),
    }
    # Strip again — final repair / cadence may have re-introduced '?'
    narrative = strip_stray_questions(narrative)
    narrative["full_script"] = " ".join(c["text"] for c in narrative["clauses"]).strip()

    # 5) Visuals
    t = time.time()
    visuals = visual_stage(
        settings, niche=niche, clauses=narrative["clauses"],
        timeout_s=timeout_s, previous_error=previous_error,
    )
    debug["stages"]["5_visuals"] = {"time_s": round(time.time() - t, 1)}

    plan = merge_and_validate(
        narrative=narrative, visuals=visuals, niche=niche, topic=topic,
    )
    return plan, debug
