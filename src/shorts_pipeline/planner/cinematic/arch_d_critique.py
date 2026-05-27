"""Architecture D — DRAFT → SELF-CRITIQUE → REWRITE.

The model writes a rough draft, then grades its own work against a rubric,
then rewrites with the critique baked in. Lets the model self-correct without
human feedback.

Stages:
  1. Rough draft (14 clauses, low constraint)
  2. Self-critique against rubric (word count, banned, image rules, identity, rhythm)
  3. Rewrite addressing critique
  4. Visuals
"""

from __future__ import annotations

import time

from shorts_pipeline.config.settings import Settings
from shorts_pipeline.planner.multistage import parse_stage_a
from shorts_pipeline.planner.niche_caps import caps_for, count_syllables
from shorts_pipeline.planner.niches_compact import COMPACT_DATA

from ._shared import (
    apply_latinate_swaps,
    find_narrative_issues,
    hard_rules_block,
    llm_post,
    merge_and_validate,
    repair_narrative,
    strip_ai_phrases,
    visual_stage,
)


def _stage1_draft(
    settings: Settings, niche: str, topic: str, timeout_s: float,
) -> dict:
    """Rough draft — no strict constraints, just write the narration."""
    min_w, max_w, _ = caps_for(niche)
    d = COMPACT_DATA[niche]
    sys_p = f"""You write {d['voice']}.

Topic: {topic!r}
Niche scope: {d['scope']}.
Transformation: {d['transformation']}.

Write a ROUGH DRAFT of a 14-clause YouTube Short narration. Aim for {min_w}-{max_w}
words total. Clause 1 starts with a curiosity-gap question (no name in clause 1-2).
END_QUESTION is the only other '?'.

Don't worry about perfection yet — get the story on the page.

OUTPUT FORMAT:

HISTORICAL_FIGURE: <name>
COLD_OPEN_OBJECT: <one object>
LEVER_TYPE: law | geography | politics
LEVER_DESC: <one sentence>
LEVER_CONSEQ: <one sentence>
LUT: epic_warm | tragic_cold | ancient_sepia | dark_thriller | golden_hour
END_QUESTION: <one '?' question>

CLAUSE 1: <text>
...
CLAUSE 14: <text>"""
    user_p = f"Rough draft for {topic!r}."
    raw = llm_post(settings, system=sys_p, user=user_p,
                   max_tokens=12000, timeout_s=timeout_s, temperature=0.5)
    parsed = parse_stage_a(raw)
    if len([c for c in parsed["clauses"] if c["text"]]) < 14:
        n = len([c for c in parsed['clauses'] if c['text']])
        raise RuntimeError(f"stage1 draft: only {n}/14 clauses parsed")
    return parsed


def _build_rubric_findings(draft: dict, niche: str) -> str:
    """Compute mechanical findings to give the LLM concrete fixes."""
    min_w, max_w, max_syl = caps_for(niche)
    full = draft["full_script"]
    w = len(full.split())
    s = count_syllables(full)
    findings = []
    if w < min_w:
        findings.append(f"WORD COUNT: only {w} words; need at least {min_w}. Add detail to weakest 2 clauses.")
    elif w > max_w:
        findings.append(f"WORD COUNT: {w} words exceeds {max_w}. Trim adjectives/filler from longest 2 clauses.")
    if s > max_syl:
        findings.append(f"SYLLABLES: {s} exceeds {max_syl}. Swap heavy Latinate words for shorter equivalents.")
    # Banned phrase scan
    from ._shared import contains_ai_phrases
    ai = contains_ai_phrases(full)
    if ai:
        findings.append(f"AI PHRASES present: {', '.join(ai[:5])}. Remove them.")
    # Identity reveal check — name in clause 1-2 is a problem
    name = draft.get("historical_figure", "").strip()
    if name:
        first_two = " ".join(draft["clauses"][i]["text"] for i in range(2)).lower()
        if name.lower() in first_two:
            findings.append(f"IDENTITY: name {name!r} appears in clauses 1-2. Delay reveal to clause 3-4.")
    if not findings:
        findings.append("No mechanical issues. Improve sentence-energy variation and curiosity-gap pacing.")
    return "\n".join(f"- {f}" for f in findings)


def _stage2_critique_and_rewrite(
    settings: Settings, niche: str, topic: str, draft: dict, timeout_s: float,
) -> dict:
    """Single LLM call that gets draft + findings and produces the rewrite."""
    min_w, max_w, max_syl = caps_for(niche)
    d = COMPACT_DATA[niche]
    findings = _build_rubric_findings(draft, niche)
    draft_block = "\n".join(
        f"CLAUSE {i+1}: {c['text']}" for i, c in enumerate(draft["clauses"])
    )
    sys_p = f"""You are revising your own draft.

Voice: {d['voice']}.

FINDINGS from automated review:
{findings}

PRINCIPLES TO APPLY:
- Sentence energy alternation: short hit / medium explain / short punch / long line.
- Banned: suddenly, against all odds, everything changed, history would remember,
  little did they know, in that moment, but then, changed history, shaped the world.

{hard_rules_block(min_w, max_w, max_syl)}

DRAFT TO FIX:
{draft_block}

Rewrite the 14 clauses addressing every finding. Keep the existing
HISTORICAL_FIGURE / COLD_OPEN_OBJECT / decision_lever / LUT / END_QUESTION values
unless you must change them. Output in the same format.

OUTPUT FORMAT:

HISTORICAL_FIGURE: <name>
COLD_OPEN_OBJECT: <one object>
LEVER_TYPE: law | geography | politics
LEVER_DESC: <one sentence>
LEVER_CONSEQ: <one sentence>
LUT: epic_warm | tragic_cold | ancient_sepia | dark_thriller | golden_hour
END_QUESTION: <one '?' question>

CLAUSE 1: <text>
...
CLAUSE 14: <text>"""
    user_p = f"Rewrite the draft to address every finding above."
    raw = llm_post(settings, system=sys_p, user=user_p,
                   max_tokens=12000, timeout_s=timeout_s, temperature=0.2)
    parsed = parse_stage_a(raw)
    if len([c for c in parsed["clauses"] if c["text"]]) < 14:
        n = len([c for c in parsed['clauses'] if c['text']])
        raise RuntimeError(f"stage2 rewrite: only {n}/14 clauses parsed")
    return parsed


def generate(
    settings: Settings,
    *,
    niche: str,
    topic: str,
    timeout_s: float = 600.0,
    previous_error: str | None = None,
) -> tuple[dict, dict]:
    debug: dict = {"arch": "D_critique", "stages": {}}
    if previous_error:
        debug["feedback_received"] = previous_error[:300]

    t = time.time()
    draft = _stage1_draft(settings, niche, topic, timeout_s)
    debug["stages"]["1_draft"] = {
        "time_s": round(time.time() - t, 1),
        "words": len(draft["full_script"].split())}

    t = time.time()
    revised = _stage2_critique_and_rewrite(settings, niche, topic, draft, timeout_s)
    debug["stages"]["2_rewrite"] = {
        "time_s": round(time.time() - t, 1),
        "words": len(revised["full_script"].split())}

    for i, c in enumerate(revised["clauses"]):
        text = strip_ai_phrases(apply_latinate_swaps(c["text"])).strip()
        revised["clauses"][i]["text"] = text
    revised["full_script"] = " ".join(c["text"] for c in revised["clauses"]).strip()

    from shorts_pipeline.planner.niche_caps import caps_for
    min_w, max_w, _ = caps_for(niche)
    issues = find_narrative_issues(revised, min_w, max_w)
    if issues:
        t = time.time()
        revised = repair_narrative(
            settings, narrative=revised, issues=issues,
            niche=niche, timeout_s=timeout_s)
        debug["stages"]["2b_repair"] = {
            "time_s": round(time.time() - t, 1),
            "issues_addressed": issues[:3],
            "words_after": len(revised["full_script"].split()),
        }

    t = time.time()
    visuals = visual_stage(
        settings, niche=niche, clauses=revised["clauses"],
        timeout_s=timeout_s, previous_error=previous_error)
    debug["stages"]["3_visuals"] = {"time_s": round(time.time() - t, 1)}

    plan = merge_and_validate(
        narrative=revised, visuals=visuals, niche=niche, topic=topic)
    return plan, debug
