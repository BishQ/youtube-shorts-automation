"""Architecture B — RETENTION-FIRST INVERTED.

Place the 4 retention spikes FIRST (the moments viewers must not skip),
then write connecting clauses AROUND them. Inverts traditional drafting.

Stages:
  1. Emotion + 4 retention spike payloads at fixed time markers
  2. Connect: write 14 clauses that pass through each spike
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


def _stage1_spikes(
    settings: Settings, niche: str, topic: str, timeout_s: float,
) -> dict:
    """Pick ONE core emotion + 4 retention spike payloads at 0/15/30/45s."""
    d = COMPACT_DATA[niche]
    sys_p = f"""You are designing retention architecture for a 60-second YouTube Short.

Topic: {topic!r}
Niche voice: {d['voice']}.

STEP 1 — Pick ONE dominant emotion from this set: dread, rage, obsession, sacrifice,
betrayal, insanity, awe, tragedy. Everything in the script must support it.

STEP 2 — Design 4 retention spikes at fixed time markers. Each spike is one short
declarative sentence (5-12 words) that creates a reveal, contradiction, scale-jump,
or emotional flip. The viewer must not skip past these.

SPIKE TYPES: reveal | contradiction | scale-jump | flip

OUTPUT FORMAT — exactly this, no JSON, no markdown:

EMOTION: <one word>
LUT: epic_warm | tragic_cold | ancient_sepia | dark_thriller | golden_hour

SPIKE 0s   TYPE=reveal           : <sentence>
SPIKE 15s  TYPE=contradiction    : <sentence>
SPIKE 30s  TYPE=scale-jump       : <sentence>
SPIKE 45s  TYPE=flip             : <sentence>"""
    user_p = f"Topic: {topic!r}. Design the 4 retention spikes."
    raw = llm_post(settings, system=sys_p, user=user_p,
                   max_tokens=3000, timeout_s=timeout_s, temperature=0.35)
    spikes: list[dict] = []
    emotion = "tragedy"
    lut = "epic_warm"
    for line in raw.splitlines():
        line = line.strip().strip("*_`")
        m_em = re.match(r"^EMOTION\s*:\s*(\w+)", line, re.IGNORECASE)
        if m_em:
            emotion = m_em.group(1).lower()
            continue
        m_lut = re.match(r"^LUT\s*:\s*(\w+)", line, re.IGNORECASE)
        if m_lut:
            lut_v = m_lut.group(1).lower()
            if lut_v in ("epic_warm", "tragic_cold", "ancient_sepia", "dark_thriller", "golden_hour"):
                lut = lut_v
            continue
        m_sp = re.match(
            r"^SPIKE\s*(\d+)s?\s+TYPE\s*=\s*([a-z\-]+)\s*:\s*(.+)$",
            line, re.IGNORECASE)
        if m_sp:
            spikes.append({
                "time_s": int(m_sp.group(1)),
                "type": m_sp.group(2).lower(),
                "text": m_sp.group(3).strip(),
            })
    if len(spikes) < 4:
        raise RuntimeError(f"stage1 spikes: only {len(spikes)}/4 parsed")
    return {"emotion": emotion, "lut": lut, "spikes": spikes[:4]}


def _stage2_connect(
    settings: Settings, niche: str, topic: str, spike_data: dict, timeout_s: float,
) -> dict:
    """Write 14 clauses that pass through each retention spike."""
    min_w, max_w, max_syl = caps_for(niche)
    d = COMPACT_DATA[niche]
    spikes = spike_data["spikes"]
    spike_block = "\n".join(
        f"  At {s['time_s']}s — {s['type'].upper()}: {s['text']}"
        for s in spikes
    )
    emotion = spike_data["emotion"]
    lut = spike_data["lut"]
    sys_p = f"""You are writing the 14 spoken clauses for a 60-second Short.

Voice: {d['voice']}.
DOMINANT EMOTION: {emotion}. Every line supports this feeling. Mix nothing else in.

These RETENTION SPIKES are FIXED — each clause near the time marker MUST contain
or lead directly into the spike line. Build the narrative around them.

{spike_block}

CLAUSE TIMING — each clause is about 4s of audio (~13 words):
  Clauses 1-3  → 0-12s   (spike at 0s lives in clause 1)
  Clauses 4-7  → 12-28s  (spike at 15s lives near clause 5)
  Clauses 8-11 → 28-44s  (spike at 30s lives near clause 9)
  Clauses 12-14→ 44-60s  (spike at 45s lives near clause 13)

Lut: {lut}.  Niche transformation: {d['transformation']}.
Sentence energy alternation: short hit / medium explain / short punch / long line.
Banned phrases: suddenly, against all odds, everything changed, history would remember,
  little did they know, in that moment, but then, changed history, shaped the world.

{hard_rules_block(min_w, max_w, max_syl)}

OUTPUT FORMAT:

HISTORICAL_FIGURE: <name>
COLD_OPEN_OBJECT: <one object>
LEVER_TYPE: law | geography | politics
LEVER_DESC: <one sentence>
LEVER_CONSEQ: <one sentence>
LUT: {lut}
END_QUESTION: <one '?' question>

CLAUSE 1: <text>
...
CLAUSE 14: <text>"""
    user_p = f"Topic: {topic!r}. Write the 14 clauses with retention spikes anchored."
    raw = llm_post(settings, system=sys_p, user=user_p,
                   max_tokens=12000, timeout_s=timeout_s)
    parsed = parse_stage_a(raw)
    if len([c for c in parsed["clauses"] if c["text"]]) < 14:
        n = len([c for c in parsed['clauses'] if c['text']])
        raise RuntimeError(f"stage2 connect: only {n}/14 clauses parsed")
    return parsed


def generate(
    settings: Settings,
    *,
    niche: str,
    topic: str,
    timeout_s: float = 600.0,
    previous_error: str | None = None,
) -> tuple[dict, dict]:
    debug: dict = {"arch": "B_retention", "stages": {}}
    if previous_error:
        debug["feedback_received"] = previous_error[:300]

    t = time.time()
    spike_data = _stage1_spikes(settings, niche, topic, timeout_s)
    debug["stages"]["1_spikes"] = {
        "time_s": round(time.time() - t, 1),
        "emotion": spike_data["emotion"],
        "lut": spike_data["lut"],
        "spike_count": len(spike_data["spikes"]),
    }

    t = time.time()
    narrative = _stage2_connect(settings, niche, topic, spike_data, timeout_s)
    debug["stages"]["2_connect"] = {
        "time_s": round(time.time() - t, 1),
        "words": len(narrative["full_script"].split())}

    # Python passes: latinate swap + AI phrase strip
    for i, c in enumerate(narrative["clauses"]):
        text = strip_ai_phrases(apply_latinate_swaps(c["text"])).strip()
        narrative["clauses"][i]["text"] = text
    narrative["full_script"] = " ".join(c["text"] for c in narrative["clauses"]).strip()

    # Validator + repair pass
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
