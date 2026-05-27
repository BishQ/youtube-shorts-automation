"""Architecture C — CURIOSITY LADDER.

14 clauses as a deliberate information ladder. Each rung reveals exactly ONE
new fact / image, never more. Identity is revealed only after the impossible
situation has landed.

Stages:
  1. Pick 14 ladder rungs (one info-unit per rung, ordered for maximum tension)
  2. Convert rungs to spoken clauses
  3. Visuals
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
    find_narrative_issues,
    hard_rules_block,
    llm_post,
    merge_and_validate,
    repair_narrative,
    strip_ai_phrases,
    visual_stage,
)


def _stage1_rungs(
    settings: Settings, niche: str, topic: str, timeout_s: float,
) -> list[str]:
    """14 numbered curiosity rungs — each adds ONE new info unit."""
    d = COMPACT_DATA[niche]
    sys_p = f"""You design the curiosity-gap ladder for a 60-second YouTube Short.

Topic: {topic!r}
Niche voice: {d['voice']}.

A LADDER has 14 rungs. Each rung reveals EXACTLY one new fact or image.
Lower rungs build mystery. Higher rungs reveal identity, stakes, consequence.
Do not reveal the subject's name before rung 3-4.

RUNG ORDER (strict):
  1. Hook question — role + impossible situation, no name
  2. Continue mystery — what makes it impossible
  3. Identity reveal — say the name
  4. Surface stakes
  5. Hidden stakes
  6. The decision moment
  7. The cost of the decision
  8. The first consequence
  9. The wider consequence
  10. The contradiction (the official story was wrong)
  11. The voice of a contemporary record
  12. The number that survives
  13. The macro reframe
  14. END_QUESTION setup — leave the viewer thinking

Each rung: ONE clear info unit, max 14 words, no fluff.

OUTPUT FORMAT — exactly 14 lines, prefixed RUNG N:, nothing else:

RUNG 1: <text>
RUNG 2: <text>
...
RUNG 14: <text>"""
    user_p = f"Topic: {topic!r}. Build the 14-rung curiosity ladder."
    raw = llm_post(settings, system=sys_p, user=user_p,
                   max_tokens=4000, timeout_s=timeout_s, temperature=0.3)
    rungs: dict[int, str] = {}
    for m in re.finditer(r"^\s*[*_>\-\s]*RUNG\s*(\d{1,2})\s*[:.\-]\s*(.+)$",
                          raw, re.MULTILINE | re.IGNORECASE):
        n = int(m.group(1))
        if 1 <= n <= 14:
            rungs[n] = m.group(2).strip().strip("*_`")
    if len(rungs) < 14:
        raise RuntimeError(f"stage1 ladder: only {len(rungs)}/14 rungs parsed")
    return [rungs[i] for i in range(1, 15)]


def _stage2_speakify(
    settings: Settings, niche: str, topic: str, rungs: list[str], timeout_s: float,
) -> dict:
    """Convert each rung to a spoken-cadence clause with full metadata."""
    min_w, max_w, max_syl = caps_for(niche)
    d = COMPACT_DATA[niche]
    rung_block = "\n".join(f"  RUNG {i+1}: {r}" for i, r in enumerate(rungs))
    sys_p = f"""You are converting a 14-rung curiosity ladder into spoken cadence.

Voice: {d['voice']}.

The 14 INFO UNITS — one per clause, IN ORDER:
{rung_block}

Clause N = the spoken form of RUNG N.
Sentence energy: short / medium / short / long / medium, never all-medium.
Banned: suddenly, against all odds, everything changed, history would remember,
  little did they know, in that moment, but then, changed history, shaped the world.

{hard_rules_block(min_w, max_w, max_syl)}

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
    user_p = f"Topic: {topic!r}. Convert the ladder to 14 spoken clauses."
    raw = llm_post(settings, system=sys_p, user=user_p,
                   max_tokens=12000, timeout_s=timeout_s)
    parsed = parse_stage_a(raw)
    if len([c for c in parsed["clauses"] if c["text"]]) < 14:
        n = len([c for c in parsed['clauses'] if c['text']])
        raise RuntimeError(f"stage2 speakify: only {n}/14 clauses parsed")
    return parsed


def generate(
    settings: Settings,
    *,
    niche: str,
    topic: str,
    timeout_s: float = 600.0,
    previous_error: str | None = None,
) -> tuple[dict, dict]:
    debug: dict = {"arch": "C_curiosity", "stages": {}}
    if previous_error:
        debug["feedback_received"] = previous_error[:300]

    t = time.time()
    rungs = _stage1_rungs(settings, niche, topic, timeout_s)
    debug["stages"]["1_ladder"] = {
        "time_s": round(time.time() - t, 1), "rung_count": len(rungs)}

    t = time.time()
    narrative = _stage2_speakify(settings, niche, topic, rungs, timeout_s)
    debug["stages"]["2_speakify"] = {
        "time_s": round(time.time() - t, 1),
        "words": len(narrative["full_script"].split())}

    for i, c in enumerate(narrative["clauses"]):
        text = strip_ai_phrases(apply_latinate_swaps(c["text"])).strip()
        narrative["clauses"][i]["text"] = text
    narrative["full_script"] = " ".join(c["text"] for c in narrative["clauses"]).strip()

    from shorts_pipeline.planner.niche_caps import caps_for
    min_w, max_w, _ = caps_for(niche)
    issues = find_narrative_issues(narrative, min_w, max_w)
    if issues:
        t = time.time()
        narrative = repair_narrative(
            settings, narrative=narrative, issues=issues,
            niche=niche, timeout_s=timeout_s)
        debug["stages"]["2b_repair"] = {
            "time_s": round(time.time() - t, 1),
            "issues_addressed": issues[:3],
            "words_after": len(narrative["full_script"].split()),
        }

    t = time.time()
    visuals = visual_stage(
        settings, niche=niche, clauses=narrative["clauses"],
        timeout_s=timeout_s, previous_error=previous_error)
    debug["stages"]["3_visuals"] = {"time_s": round(time.time() - t, 1)}

    plan = merge_and_validate(
        narrative=narrative, visuals=visuals, niche=niche, topic=topic)
    return plan, debug
