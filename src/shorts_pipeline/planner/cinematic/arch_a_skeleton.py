"""Architecture A — SKELETON → EXPAND → COMPRESS → VOICE → VISUALS.

5 stages of refinement, each focused on one craft pass.
Inspired by how human writers actually draft cinematic narration.
"""

from __future__ import annotations

import re
import time

from shorts_pipeline.config.settings import Settings
from shorts_pipeline.planner.multistage import parse_stage_a
from shorts_pipeline.planner.niche_caps import caps_for
from shorts_pipeline.planner.niches_compact import COMPACT_DATA

from ._shared import (
    apply_latinate_swaps,
    contains_ai_phrases,
    find_narrative_issues,
    hard_rules_block,
    llm_post,
    merge_and_validate,
    repair_narrative,
    strip_ai_phrases,
    visual_stage,
)


def _stage1_skeleton(settings: Settings, niche: str, topic: str, timeout_s: float) -> list[str]:
    """5 beats only. ONE line each. Hook | Conflict | Escalation | Twist | Payoff."""
    d = COMPACT_DATA[niche]
    sys_p = f"""You are a cinematic story architect.

Topic: {topic!r}
Niche voice: {d['voice']}.
Niche scope: {d['scope']}.

Write 5 STORY BEATS — one line each, 8-15 words max per line.
Each beat is a step in a curiosity-gap ladder. Do NOT name the subject in beat 1.

BEAT 1 — HOOK         (role + impossible situation, no name)
BEAT 2 — CONFLICT     (the stakes appear)
BEAT 3 — ESCALATION   (the choice that can't be undone)
BEAT 4 — TWIST        (the contradiction or reveal)
BEAT 5 — PAYOFF       (consequence + emotional landing)

Output EXACTLY these five lines, prefixed BEAT 1: through BEAT 5:. Nothing else."""
    user_p = f"Topic: {topic!r}. Write the 5 beats."
    raw = llm_post(settings, system=sys_p, user=user_p,
                   max_tokens=3000, timeout_s=timeout_s, temperature=0.4)
    beats: list[str] = []
    for m in re.finditer(r"BEAT\s*[15]?\d?\s*[:.\-]\s*(.+)", raw):
        beats.append(m.group(1).strip().strip("*_`"))
    if len(beats) < 5:
        # Fallback: take first 5 non-empty lines
        beats = [l.strip().lstrip("0123456789.)-*_ ").strip()
                 for l in raw.splitlines() if l.strip()][:5]
    if len(beats) < 5:
        raise RuntimeError(f"stage1 skeleton: only {len(beats)}/5 beats parsed")
    return beats[:5]


def _stage2_expand(
    settings: Settings, niche: str, topic: str, beats: list[str], timeout_s: float,
) -> dict:
    """Expand 5 beats into 14 clauses with all narrative metadata."""
    min_w, max_w, max_syl = caps_for(niche)
    d = COMPACT_DATA[niche]
    beat_block = "\n".join(f"  BEAT {i+1}: {b}" for i, b in enumerate(beats))
    sys_p = f"""You are writing the spoken narration for a YouTube Short.

Voice: {d['voice']}.
The 5 BEATS to expand into 14 clauses:
{beat_block}

Spread the 5 beats across 14 clauses (~3 clauses per beat).
Sentence energy alternation: vary short/medium/long. Avoid all-medium pacing.
Niche transformation (decision_lever.description): {d['transformation']}.

{hard_rules_block(min_w, max_w, max_syl)}

OUTPUT FORMAT — labelled text, no JSON:

HISTORICAL_FIGURE: <name>
COLD_OPEN_OBJECT: <one object>
LEVER_TYPE: law | geography | politics
LEVER_DESC: <one sentence>
LEVER_CONSEQ: <one sentence>
LUT: epic_warm | tragic_cold | ancient_sepia | dark_thriller | golden_hour
END_QUESTION: <one question ending in '?'>

CLAUSE 1: <hook question, no name>
CLAUSE 2: <continue mystery, no name>
CLAUSE 3: <reveal identity here>
CLAUSE 4: <text>
...
CLAUSE 14: <text>"""
    user_p = f"Expand the 5 beats into 14 clauses for topic {topic!r}. Hit the word budget."
    raw = llm_post(settings, system=sys_p, user=user_p,
                   max_tokens=12000, timeout_s=timeout_s)
    parsed = parse_stage_a(raw)
    if len([c for c in parsed["clauses"] if c["text"]]) < 14:
        n = len([c for c in parsed['clauses'] if c['text']])
        raise RuntimeError(f"stage2 expand: only {n}/14 clauses parsed")
    return parsed


def _stage3_compress(narrative: dict, niche: str) -> dict:
    """Pure-Python compression pass: Latinate swaps + word-count check.

    Returns the modified narrative. Doesn't call the LLM unless we add a
    cleanup-stage LLM call. For now, mechanical pass only.
    """
    for i, c in enumerate(narrative["clauses"]):
        text = apply_latinate_swaps(c["text"])
        narrative["clauses"][i]["text"] = text
    narrative["full_script"] = " ".join(c["text"] for c in narrative["clauses"]).strip()
    return narrative


def _stage4_voice(
    settings: Settings, niche: str, narrative: dict, timeout_s: float,
) -> dict:
    """Voice pass: rewrite for spoken cinematic narration with hard rhythm.

    Skip the LLM call if anti-AI phrases are clean AND no rewrites needed.
    Otherwise: strip AI phrases mechanically, no LLM round-trip.
    """
    txt = narrative["full_script"]
    ai_hits = contains_ai_phrases(txt)
    if ai_hits:
        for i, c in enumerate(narrative["clauses"]):
            narrative["clauses"][i]["text"] = strip_ai_phrases(c["text"]).strip()
        narrative["full_script"] = " ".join(c["text"] for c in narrative["clauses"]).strip()
    return narrative


def generate(
    settings: Settings,
    *,
    niche: str,
    topic: str,
    timeout_s: float = 600.0,
    previous_error: str | None = None,
) -> tuple[dict, dict]:
    debug: dict = {"arch": "A_skeleton", "stages": {}}
    if previous_error:
        debug["feedback_received"] = previous_error[:300]

    # Stage 1
    t = time.time()
    beats = _stage1_skeleton(settings, niche, topic, timeout_s)
    debug["stages"]["1_skeleton"] = {
        "time_s": round(time.time() - t, 1), "beats": beats}

    # Stage 2
    t = time.time()
    narrative = _stage2_expand(settings, niche, topic, beats, timeout_s)
    debug["stages"]["2_expand"] = {
        "time_s": round(time.time() - t, 1),
        "words": len(narrative["full_script"].split())}

    # Stage 3 — compression (Python only)
    t = time.time()
    narrative = _stage3_compress(narrative, niche)
    debug["stages"]["3_compress"] = {
        "time_s": round(time.time() - t, 2),
        "words": len(narrative["full_script"].split())}

    # Stage 4 — voice / anti-AI sweep
    t = time.time()
    narrative = _stage4_voice(settings, niche, narrative, timeout_s)
    debug["stages"]["4_voice"] = {"time_s": round(time.time() - t, 2)}

    # Stage 4b — validator + repair pass (focused, single shot)
    from shorts_pipeline.planner.niche_caps import caps_for
    min_w, max_w, _ = caps_for(niche)
    issues = find_narrative_issues(narrative, min_w, max_w)
    if issues:
        t = time.time()
        narrative = repair_narrative(
            settings, narrative=narrative, issues=issues,
            niche=niche, timeout_s=timeout_s)
        debug["stages"]["4b_repair"] = {
            "time_s": round(time.time() - t, 1),
            "issues_addressed": issues[:3],
            "words_after": len(narrative["full_script"].split()),
        }

    # Stage 5 — visuals
    t = time.time()
    visuals = visual_stage(
        settings, niche=niche, clauses=narrative["clauses"],
        timeout_s=timeout_s, previous_error=previous_error,
    )
    debug["stages"]["5_visuals"] = {"time_s": round(time.time() - t, 1)}

    plan = merge_and_validate(
        narrative=narrative, visuals=visuals, niche=niche, topic=topic)
    return plan, debug
