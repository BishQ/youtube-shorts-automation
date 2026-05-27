"""Architecture E — CONSTRAINT-LOCK.

Word count and structural constraints are LOCKED before writing. The model
allocates a word budget per clause first, then writes each clause to its
assigned length. Eliminates SHORT/LONG word-count failures.

Stages:
  1. Allocate word budget per clause (14 numbers summing to target)
  2. Write each clause to its assigned length, in order, with strict role rules
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


def _allocate_word_budget(min_w: int, max_w: int) -> list[int]:
    """Generate 14 word counts summing to the centre of [min_w, max_w].

    Pattern (sentence energy alternation): short / medium / short / long / medium.
    """
    target = (min_w + max_w) // 2
    # 14-clause pattern: short(9) / med(13) / short(10) / long(17) / med(13) ...
    pattern = [9, 13, 10, 17, 13, 10, 15, 11, 14, 12, 16, 11, 14, 12]
    base_sum = sum(pattern)
    # Scale so sum == target
    scale = target / base_sum
    allocated = [max(7, round(p * scale)) for p in pattern]
    # Adjust to hit exact target by tweaking the longest
    diff = target - sum(allocated)
    if diff != 0:
        idx_long = allocated.index(max(allocated))
        allocated[idx_long] += diff
    return allocated


def _stage1_allocate_skeleton(
    settings: Settings, niche: str, topic: str, timeout_s: float,
) -> dict:
    """Python pre-allocates word budget per clause; LLM picks emotion + lut + figure name."""
    d = COMPACT_DATA[niche]
    sys_p = f"""You design the structural shell for a 14-clause YouTube Short.

Topic: {topic!r}
Niche: {d['voice']}.
Transformation: {d['transformation']}.

Output ONLY the structural fields (no clauses, no narrative):

HISTORICAL_FIGURE: <subject's name>
COLD_OPEN_OBJECT: <one physical object, no person>
LEVER_TYPE: law | geography | politics
LEVER_DESC: <one sentence describing the decision moment>
LEVER_CONSEQ: <one sentence describing the consequence>
LUT: epic_warm | tragic_cold | ancient_sepia | dark_thriller | golden_hour
END_QUESTION: <one rhetorical question ending in '?'>

Five lines plus those seven labelled fields. Nothing else."""
    user_p = f"Topic: {topic!r}. Design the shell."
    raw = llm_post(settings, system=sys_p, user=user_p,
                   max_tokens=3000, timeout_s=timeout_s, temperature=0.3)
    parsed = parse_stage_a(raw)
    # Don't need clauses yet
    if not parsed.get("historical_figure"):
        # Use topic as fallback figure
        parsed["historical_figure"] = topic[:80]
    return parsed


def _stage2_write_each(
    settings: Settings, niche: str, topic: str, shell: dict,
    budgets: list[int], timeout_s: float,
) -> dict:
    """Single LLM call: write all 14 clauses with the budget enforced."""
    min_w, max_w, max_syl = caps_for(niche)
    d = COMPACT_DATA[niche]
    figure = shell.get("historical_figure", topic)
    budget_block = "\n".join(
        f"  CLAUSE {i+1}: target {b} words"
        for i, b in enumerate(budgets)
    )
    sys_p = f"""You write {d['voice']}.

CRITICAL — WORD BUDGET PER CLAUSE (locked):
{budget_block}

Each clause MUST land within ±2 words of its target. Total: {sum(budgets)} words.

STRUCTURE / ROLE BY CLAUSE:
  Clause 1: hook question, role only, NO name (must include '?').
  Clause 2: deepen the mystery, no name yet.
  Clause 3-4: identity reveal — first place the name {figure!r} can appear.
  Clause 5-8: stakes and the irreversible decision.
  Clause 9-11: cost and contradictions.
  Clause 12-14: macro reframe + setup the END_QUESTION.

ENERGY: alternate short/medium/long (already encoded in target lengths).
Banned phrases: suddenly, against all odds, everything changed, history would remember,
  little did they know, in that moment, but then, changed history, shaped the world.

{hard_rules_block(min_w, max_w, max_syl)}

OUTPUT FORMAT — clauses ONLY (shell fields are already locked):

CLAUSE 1: <text — exactly {budgets[0]} words ±2>
CLAUSE 2: <text — exactly {budgets[1]} words ±2>
...
CLAUSE 14: <text — exactly {budgets[13]} words ±2>"""
    user_p = f"Write the 14 clauses for {topic!r} to the locked budgets above."
    raw = llm_post(settings, system=sys_p, user=user_p,
                   max_tokens=12000, timeout_s=timeout_s, temperature=0.2)
    parsed = parse_stage_a(raw)
    if len([c for c in parsed["clauses"] if c["text"]]) < 14:
        # Try harder — also accept simple numbered fallback (already in parse_stage_a)
        n = len([c for c in parsed['clauses'] if c['text']])
        raise RuntimeError(f"stage2 write: only {n}/14 clauses parsed")
    # Merge shell + new clauses
    parsed["historical_figure"] = shell.get("historical_figure") or parsed.get("historical_figure")
    parsed["cold_open_object"] = shell.get("cold_open_object") or parsed.get("cold_open_object")
    parsed["decision_lever"] = shell.get("decision_lever") or parsed.get("decision_lever")
    parsed["lut_choice"] = shell.get("lut_choice") or parsed.get("lut_choice")
    parsed["end_plate_question"] = shell.get("end_plate_question") or parsed.get("end_plate_question")
    parsed["full_script"] = " ".join(c["text"] for c in parsed["clauses"]).strip()
    return parsed


def generate(
    settings: Settings,
    *,
    niche: str,
    topic: str,
    timeout_s: float = 600.0,
    previous_error: str | None = None,
) -> tuple[dict, dict]:
    debug: dict = {"arch": "E_constraint", "stages": {}}
    if previous_error:
        debug["feedback_received"] = previous_error[:300]

    min_w, max_w, _ = caps_for(niche)
    budgets = _allocate_word_budget(min_w, max_w)
    debug["budgets"] = {"per_clause": budgets, "total": sum(budgets),
                        "target_range": [min_w, max_w]}

    t = time.time()
    shell = _stage1_allocate_skeleton(settings, niche, topic, timeout_s)
    debug["stages"]["1_shell"] = {"time_s": round(time.time() - t, 1)}

    t = time.time()
    narrative = _stage2_write_each(settings, niche, topic, shell, budgets, timeout_s)
    debug["stages"]["2_write"] = {
        "time_s": round(time.time() - t, 1),
        "words": len(narrative["full_script"].split())}

    for i, c in enumerate(narrative["clauses"]):
        text = strip_ai_phrases(apply_latinate_swaps(c["text"])).strip()
        narrative["clauses"][i]["text"] = text
    narrative["full_script"] = " ".join(c["text"] for c in narrative["clauses"]).strip()

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
