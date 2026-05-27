"""Multi-stage narration plan generator — TEXT output, Python-side parsing.

Small local models (Gemma 4, etc.) struggle to produce well-formed JSON under
strict schema. We instead ask them for plain labelled text (a format every
model handles fluently) and parse it ourselves into the NarrationPlan shape.

Stage A — Narrative (plain text)
  Out: HISTORICAL_FIGURE, COLD_OPEN_OBJECT, DECISION_LEVER/DESC/CONSEQ,
       LUT, END_QUESTION, CLAUSE 1..14

Stage B — Visual + Motion + Beat per clause (plain text)
  Out: per clause: IMAGE / MOTION / EMOTION / INTENSITY / CAMERA / TRANSITION
       / DURATION / COLOR / AUDIO / EMPHASIS / TIER / SUBTITLE / CUT_TARGET

Final assembly -> NarrationPlan dict -> validated.
"""

from __future__ import annotations

import re
import time
from typing import Any

import httpx

from shorts_pipeline.config.settings import Settings
from shorts_pipeline.planner.niche_caps import caps_for
from shorts_pipeline.planner.niches_compact import COMPACT_DATA
from shorts_pipeline.planner.schema import NarrationPlan


_THINK_RE = re.compile(r"<think(?:ing)?>.*?</think(?:ing)?>", re.DOTALL | re.IGNORECASE)


# ── Stage A ───────────────────────────────────────────────────────────────────

def stage_a_system(niche: str) -> str:
    d = COMPACT_DATA[niche]
    min_w, max_w, max_syl = caps_for(niche)
    banned = "; ".join(d["banned"][:7])
    return f"""You write {d['voice']}.

Write the NARRATION ONLY for a 14-clause YouTube Short (~58 sec).

SCOPE: {d['scope']}
TRANSFORMATION: {d['transformation']}

Target ~{(min_w+max_w)//2} words across 14 clauses for ~58 seconds of speech.
Mix short and long sentences for rhythm. Plain Anglo-Saxon English.

QUESTION MARKS:
  • Clause 1's first sentence ends with '?' (the curiosity hook).
  • END_QUESTION ends with '?'.
  • Clauses 2-14: no question marks.

STRUCTURE:
- Exactly 14 clauses.
- Clause 1's hook is under 14 words. Style: {d['hook_style']}

BANNED PHRASES (any = fail): {banned}; changed history; shaped the world.

CONSTRAINTS: {d['constraint']}

OUTPUT FORMAT — exactly this layout, no JSON, no markdown, no commentary:

HISTORICAL_FIGURE: <subject name>
COLD_OPEN_OBJECT: <one physical object, no person>
LEVER_TYPE: law | geography | politics
LEVER_DESC: <one sentence>
LEVER_CONSEQ: <one sentence>
LUT: <epic_warm | tragic_cold | ancient_sepia | dark_thriller | golden_hour>   # prefer {d['color_grade'].split(',')[0].strip()}
END_QUESTION: <one rhetorical question ending in '?'>

CLAUSE 1: <text — first sentence is the curiosity-gap question>
CLAUSE 2: <text>
CLAUSE 3: <text>
CLAUSE 4: <text>
CLAUSE 5: <text>
CLAUSE 6: <text>
CLAUSE 7: <text>
CLAUSE 8: <text>
CLAUSE 9: <text>
CLAUSE 10: <text>
CLAUSE 11: <text>
CLAUSE 12: <text>
CLAUSE 13: <text>
CLAUSE 14: <text>"""


def stage_a_user(topic: str) -> str:
    return (
        f"Write the narration for a YouTube Short about: {topic!r}.\n"
        f"14 clauses. Hit the word budget."
    )


# ── Stage A parser ───────────────────────────────────────────────────────────

_LABEL_RE = re.compile(r"^([A-Z_]+):\s*(.*)$")


def _strip(text: str) -> str:
    return text.strip().strip("\"'").strip()


def _strip_clause_decoration(text: str) -> str:
    """Remove markdown decorations + parenthetical word counts like '(14 words)'."""
    text = re.sub(r"\(\s*\d+\s*words?\s*\)", "", text)
    text = text.strip().strip("*_`\"' ").strip()
    return text


def parse_stage_a(raw: str) -> dict:
    """Parse Stage A's labelled text into a partial plan dict.

    Tolerant of: plain CLAUSE N: format, markdown *Clause N:*, decorated lists.
    Picks the LAST occurrence of each label so reasoning-model drafts before the
    final answer don't pollute (model often iterates).
    """
    out: dict[str, Any] = {
        "historical_figure": "",
        "cold_open_object": "",
        "decision_lever": {"lever_type": "politics", "description": "", "consequence": ""},
        "lut_choice": "epic_warm",
        "end_plate_question": "",
        "clauses": [],
        "full_script": "",
    }
    lines = raw.splitlines()
    clauses: dict[int, str] = {}
    cur_label: str | None = None
    cur_value: list[str] = []

    def flush():
        nonlocal cur_label, cur_value
        if cur_label is None:
            return
        val = " ".join(cur_value).strip()
        if cur_label == "HISTORICAL_FIGURE": out["historical_figure"] = _strip(val)
        elif cur_label == "COLD_OPEN_OBJECT": out["cold_open_object"] = _strip(val)
        elif cur_label == "LEVER_TYPE":
            t = _strip(val).lower()
            if t in ("law", "geography", "politics"):
                out["decision_lever"]["lever_type"] = t
        elif cur_label == "LEVER_DESC": out["decision_lever"]["description"] = _strip(val)
        elif cur_label == "LEVER_CONSEQ": out["decision_lever"]["consequence"] = _strip(val)
        elif cur_label == "LUT":
            t = _strip(val).lower().split()[0].split("#")[0].strip()
            if t in ("epic_warm", "tragic_cold", "ancient_sepia", "dark_thriller", "golden_hour"):
                out["lut_choice"] = t
        elif cur_label == "END_QUESTION":
            q = _strip(val)
            if not q.endswith("?"): q += "?"
            out["end_plate_question"] = q
        elif cur_label.startswith("CLAUSE_"):
            try:
                idx = int(cur_label.split("_")[1])
                clauses[idx] = _strip(val)
            except ValueError:
                pass
        cur_label = None
        cur_value = []

    # Accept many clause-header formats:
    #   CLAUSE 1: ...
    #   *Clause 1:* ...
    #   **Clause 1:** ...
    #   Clause 1. ...
    #   1. ... (when wrapped in a list of 14)
    _clause_re = re.compile(
        r"^\s*[*_#>\-\s]*\**\s*clause\s*(\d+)\s*[:.\-)]\**\s*(.*)$",
        re.IGNORECASE,
    )
    for line in lines:
        m_clause = _clause_re.match(line)
        if m_clause:
            flush()
            cur_label = f"CLAUSE_{int(m_clause.group(1))}"
            cur_value = [m_clause.group(2)]
            continue
        m = _LABEL_RE.match(line.strip())
        if m and m.group(1) in (
            "HISTORICAL_FIGURE", "COLD_OPEN_OBJECT", "LEVER_TYPE", "LEVER_DESC",
            "LEVER_CONSEQ", "LUT", "END_QUESTION",
        ):
            flush()
            cur_label = m.group(1)
            cur_value = [m.group(2)]
            continue
        # Continuation line for current label
        if cur_label is not None and line.strip():
            cur_value.append(line.strip())
    flush()

    # Fallback: if labeled format failed, look for a "1. ... 14. ..." numbered block
    if len(clauses) < 14:
        num_re = re.compile(r"^\s*[*\-_]?\s*(\d{1,2})[.\)]\s+(.+?)\s*$")
        candidates: list[dict[int, str]] = []
        cur: dict[int, str] = {}
        expected = 1
        for line in lines:
            m = num_re.match(line)
            if not m:
                if expected > 1 and len(cur) >= 14:
                    candidates.append(cur)
                if len(cur) > 0:
                    cur = {}
                    expected = 1
                continue
            idx = int(m.group(1))
            text = m.group(2).strip()
            if idx == expected:
                cur[idx] = text
                expected = idx + 1
            else:
                if len(cur) >= 14:
                    candidates.append(cur)
                cur = {idx: text} if idx == 1 else {}
                expected = idx + 1 if idx == 1 else 1
        if len(cur) >= 14:
            candidates.append(cur)
        if candidates:
            # Prefer the LAST 14-item block (often the refined version)
            best = candidates[-1]
            for i in range(1, 15):
                if i in best and i not in clauses:
                    clauses[i] = best[i]

    # Clean clause decorations / word-count annotations
    for i in list(clauses.keys()):
        clauses[i] = _strip_clause_decoration(clauses[i])

    # Order clauses 1..14
    out["clauses"] = [{"text": clauses.get(i, "")} for i in range(1, 15)]
    texts = [c["text"] for c in out["clauses"]]
    out["full_script"] = " ".join(t for t in texts if t).strip()
    return out


# ── Stage B ───────────────────────────────────────────────────────────────────

def stage_b_system(niche: str) -> str:
    d = COMPACT_DATA[niche]
    return f"""You write HOLLYWOOD-GRADE cinematic visual prompts for a 14-clause Short.
IMAGE feeds a still-image model. MOTION feeds a WAN image-to-video model.

Niche visual context: {d['visual']}

══════════════════════════════════════════════════════════════════
IMAGE — describe what's IN the frame like a film cinematographer's shot list
══════════════════════════════════════════════════════════════════

Each IMAGE must be 100-180 words. Pack it with what makes a frame haunting:

  [SHOT TYPE], [subject's full name + age/build + posture + WHAT THEIR FACE
  IS DOING + costume detail with named fabric/colour], [ONE specific
  foreground object with material + condition], [background detail that
  tells story], [named LIGHT source with quality — "single tallow candle
  guttering on a draught", "low December sun through clerestory haze",
  "single bare bulb swinging on a frayed cord"], [palette in 3 named colours].

Required ONE shot-type word from:
  close-up | wide shot | medium shot | low-angle | high-angle | establishing
  hero portrait | extreme close-up | tracking shot | top-down
  over-the-shoulder | profile silhouette | aerial shot

WHAT MAKES IT CINEMATIC (do all of these per image):
  ✓ Specific PROPS with material and wear ("brass-bound ledger spine cracked
    along the third entry", not "an old book")
  ✓ Character MICRO-EXPRESSION ("his left eyebrow lifts a fraction",
    "the corner of her mouth catches a tremor she will not show", not
    "looks angry")
  ✓ ONE textural sensory detail ("ink dried into the grain of the desk",
    "frost on the brass nailheads", "blood crusted black on the cuff")
  ✓ Costume specifics with period authenticity ("slashed black-and-cream
    doublet with starched lace ruff", not "old-fashioned clothes")
  ✓ Light has a NAMED source and behaviour (candle guttering, dawn knifing
    in, gaslight wheezing, monitor bloom)
  ✓ 3 named colours forming a palette ("oxblood + brass + soot-grey")

DO NOT use vague film-theory tags ("anamorphic flare", "soft grain",
"Caravaggio realism") on their own. The look comes from SCENE specificity,
not from style buzzwords.

Banned objects: skyscraper, smartphone, laptop, cell phone, mobile phone,
neon sign, swastika, then-and-now, morphing, comic panel, split screen.

INCLUDE the figure's real name in IMAGE prompts (the image model renders
them accurately). Open each hero shot with their full name.

GENDER MARKERS — REQUIRED for every human subject. Image models default to
male if not told. Always include explicit gender markers:
  • For women: "elderly woman" / "young woman" / "middle-aged woman",
    "feminine features", "clearly female"; optional "no beard, no male
    features" if the figure is famously confused (queens, female pioneers).
  • For men: "elderly man" / "young man" / "middle-aged man", "masculine
    features", "clearly male".
  • Age cue is mandatory ("seventy-year-old", "in her thirties", "child").
  • For figures whose appearance is well-known (Queen Elizabeth II, Genghis
    Khan, Einstein, Cleopatra), still spell out gender + age — the model is
    not reliable on names alone.

GOOD example for a queen: "Cinematic hero portrait of Queen Elizabeth II,
elderly woman in her seventies, silver-white hair set in soft waves, pearl
necklace and brooch at her collar, regal expression, dignified gaze direct
to camera, royal blue sash across her dress, clearly female with feminine
facial features, dramatic Rembrandt lighting from the left, sapphire +
cream + warm tungsten palette."

GOOD IMAGE example:
  "Low-angle medium shot of King Ferdinand II, fifty-three, in a high-collared
   black gown stiff with bullion thread, his right hand splayed across a brass-
   bound ledger whose third entry is underlined in red ink. His jaw clenches
   once — the kind of muscle-tic that betrays a man who already knows the
   answer he is being given. A single tallow candle on the corner of the desk
   guts in the cross-draught from the leaded-glass window behind him; the
   chamber beyond his shoulder is grey-blue with first dawn. Three pewter
   seals lie in a row beside his left wrist. Oxblood, brass, candle-amber."

BAD IMAGE example: "A king at his desk looking serious in old clothes, candle
light, dark thriller mood." (no specifics, no micro-expression, no props with
material/condition)

══════════════════════════════════════════════════════════════════
MOTION — choreograph 3-5 seconds of WAN i2v animation from the IMAGE
══════════════════════════════════════════════════════════════════

Each MOTION must be 50-120 words — a tiny director's note to the animator.

CRITICAL: motion begins from the EXACT state of the IMAGE.
  IMAGE: "king gripping bloodied sword beside the throne"
  ✓ MOTION: "He loosens his grip on the sword hilt by a finger's width, then
     lowers the blade slowly onto the throne armrest until the edge catches
     the carved oak. One drop of blood traces the fuller and falls. His chest
     rises once with a held breath he will not release."
  ✗ MOTION: "He walks across the battlefield." (different scene — breaks)

Structure each MOTION as THREE beats:
  Beat 1 — the FIRST physical movement (a concrete verb of the subject)
  Beat 2 — the MICRO-CHANGE (face, hand, eye-line, breath)
  Beat 3 — ONE environment response (smoke curls, dust drifts, candle dims,
           banner ripples, paper edges curl in the heat)

Use specific physical verbs:
  raises, lowers, turns, opens, closes, pours, lifts, drops, draws, signs,
  seals, kneels, stands, walks toward, reaches, grips, releases, drinks,
  exhales, blinks, leans, presses, traces, smooths, brushes, sweeps,
  staggers, steadies, tightens, slackens, presses, withdraws, advances.

Vary categories across the 14 clauses — never reuse the same verb twice.
Mix in:
  • Object interaction (sealing a letter, drawing a sword, pouring oil)
  • Body motion (kneeling, walking forward, turning the head, leaning in)
  • Subtle face motion (eyes narrow, jaw clenches, mouth tightens, brow lifts)
  • Hand detail (finger taps once, knuckles whiten, palm flattens on wood)
  • Environment beat (dust drifts, candle smoke curls, banners ripple, ash
    settles, water beads on glass, fire pops in the hearth)

GOOD MOTION example: "Ferdinand II's right index finger taps the third
underlined entry in the ledger, once, twice. His jaw sets. He closes the
ledger with the slow, deliberate weight of a verdict — the brass binding
clicks shut. The tallow candle on the desk's corner guts in the draught;
its flame stretches sideways, then steadies."

Each MOTION unique. Min 80 chars.

══════════════════════════════════════════════════════════════════
VISUAL PROGRESSION across the 14 clauses
══════════════════════════════════════════════════════════════════
  Clauses 1-4   : mystery, cold light, distance, single subject
  Clauses 5-9   : movement, decision, faces visible, escalation
  Clauses 10-14 : cost, emptiness, decay, simpler frames

Try to reuse 1-2 RECURRING OBJECTS across clauses (a sealed letter, a single
candle, an empty throne) — they create subconscious continuity.

══════════════════════════════════════════════════════════════════
OUTPUT FORMAT — exactly two fields per clause, nothing else:

CLAUSE 1:
  IMAGE: <text>
  MOTION: <text>

CLAUSE 2:
  IMAGE: <text>
  MOTION: <text>

... up through CLAUSE 14."""


def stage_b_user(clauses: list[dict]) -> str:
    lines = [f"  {i+1}. {c['text']}" for i, c in enumerate(clauses)]
    return (
        "Design the visual + motion + beat for each of these 14 clauses (in order):\n\n"
        + "\n".join(lines)
        + "\n\nProduce 14 blocks in the format above."
    )


# ── Stage B parser ───────────────────────────────────────────────────────────

_CLAUSE_HEAD_RE = re.compile(
    r"^\s*[*_#>\-\s]*\**\s*clause\s*(\d+)\s*[:.\-)]\**\s*$",
    re.IGNORECASE,
)
_FIELD_RE = re.compile(r"^\s*[*_>\-\s]*\**\s*([A-Z_]+)\s*\**\s*:\s*(.*)$")


def parse_stage_b(raw: str) -> list[dict]:
    """Parse Stage B's labelled blocks. Returns list of 14 dicts."""
    blocks: dict[int, dict[str, str]] = {}
    cur_block: dict[str, str] | None = None
    cur_idx: int | None = None

    for line in raw.splitlines():
        m_head = _CLAUSE_HEAD_RE.match(line)
        # Also support "CLAUSE 1: content" with content on same line — start fresh
        m_head_inline = re.match(
            r"^\s*[*_#>\-\s]*\**\s*clause\s*(\d+)\s*[:.\-)]\**\s*(.+)$",
            line, re.IGNORECASE,
        )
        if m_head and not m_head_inline:
            if cur_block is not None and cur_idx is not None:
                blocks[cur_idx] = cur_block
            cur_idx = int(m_head.group(1))
            cur_block = {}
            continue
        if m_head_inline:
            if cur_block is not None and cur_idx is not None:
                blocks[cur_idx] = cur_block
            cur_idx = int(m_head_inline.group(1))
            cur_block = {}
            # Could be data right after — try as field
            rest = m_head_inline.group(2)
            mfield = _FIELD_RE.match(rest)
            if mfield:
                cur_block[mfield.group(1).upper()] = mfield.group(2).strip()
            continue
        mfield = _FIELD_RE.match(line)
        if mfield and cur_block is not None:
            cur_block[mfield.group(1).upper()] = mfield.group(2).strip()
    if cur_block is not None and cur_idx is not None:
        blocks[cur_idx] = cur_block

    # Build 14 visual dicts. Engine derives beat metadata deterministically —
    # the model only provides IMAGE + MOTION.
    out: list[dict] = []
    # Visual progression arcs (engine-side, not model-side)
    EMOTION_ARC = [
        "hook", "tense_buildup", "suspense", "tense_buildup",      # 1-4 mystery
        "reveal", "climactic", "shock", "tense_buildup", "tragic", # 5-9 escalation
        "reflective", "tragic", "reflective", "climactic", "reflective",  # 10-14 cost
    ]
    INTENSITY_ARC = [
        0.90, 0.55, 0.60, 0.65,
        0.80, 0.95, 0.85, 0.70, 0.80,
        0.60, 0.75, 0.55, 0.85, 0.70,
    ]
    # Every clause has motion (no "hold"). Mix of ken_burns/pan/zoom_out/parallax.
    CAMERA_ARC = [
        "ken_burns", "pan", "ken_burns", "zoom_out",
        "pan", "ken_burns", "zoom_out", "ken_burns", "pan",
        "parallax", "ken_burns", "pan", "ken_burns", "zoom_out",
    ]
    AUDIO_ARC = [
        "low_rumble", "paper_flutter", "none", "none",
        "impact", "thunder_crack", "crowd_murmur", "none", "low_rumble",
        "none", "fire_crackle", "none", "impact", "none",
    ]
    # Smooth img-to-img transitions throughout — only clause 1 is hard_cut
    # (it's the opening and has nothing to fade FROM).
    TRANSITION_ARC = [
        "hard_cut", "xfade", "xfade", "xfade",
        "xfade", "smash_white", "dip_to_black", "xfade", "xfade",
        "xfade", "dip_to_black", "xfade", "xfade", "dip_to_black",
    ]
    TIER_ARC = [
        "legendary", "cinematic", "cinematic", "cinematic",
        "cinematic", "legendary", "legendary", "cinematic", "cinematic",
        "grounded", "cinematic", "grounded", "cinematic", "cinematic",
    ]
    SUBTITLE_ARC = [
        "middle", "bottom", "bottom", "bottom",
        "middle", "middle", "middle", "bottom", "bottom",
        "bottom", "middle", "bottom", "middle", "bottom",
    ]
    for i in range(1, 15):
        b = blocks.get(i, {})
        idx0 = i - 1
        out.append({
            "image_prompt": (b.get("IMAGE", "") or "").strip(),
            "motion_prompt": (b.get("MOTION", "") or "").strip(),
            "figure_present": True,  # default true; engine can override per niche
            "beat": {
                "emotion": EMOTION_ARC[idx0],
                "intensity": INTENSITY_ARC[idx0],
                "camera": CAMERA_ARC[idx0],
                "transition_in": TRANSITION_ARC[idx0],
                "duration_hint": "medium",
                "color_grade": None,  # use plan-level lut_choice instead
                "audio_event": AUDIO_ARC[idx0],
                "emphasis_words": [],  # engine can derive from clause text later
                "visual_tier": TIER_ARC[idx0],
                "subtitle_position": SUBTITLE_ARC[idx0],
                "cut_target": None,
            },
        })
    return out


# ── HTTP helper ──────────────────────────────────────────────────────────────


def _post(settings: Settings, payload: dict, timeout: float) -> str:
    url = settings.local_llm_base_url.rstrip("/") + "/chat/completions"
    with httpx.Client(timeout=timeout) as c:
        r = c.post(url, json=payload)
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
    msg = r.json()["choices"][0]["message"]
    content = (msg.get("content") or "").strip()
    # Reasoning models (Qwen3, DeepSeek-R1) put output in reasoning_content
    if not content:
        reasoning = (msg.get("reasoning_content") or "").strip()
        # Reasoning_content often contains the answer at the end after thinking
        content = reasoning
    return _THINK_RE.sub("", content).strip()


def _stage_payload(model: str, sys_p: str, user_p: str, *, max_tokens: int) -> dict:
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": sys_p},
            {"role": "user", "content": user_p},
        ],
        "temperature": 0.2,
        "max_tokens": max_tokens,
    }


# ── Main driver ──────────────────────────────────────────────────────────────


def generate_multistage(
    settings: Settings,
    *,
    niche: str,
    topic: str,
    timeout_s: float = 600.0,
) -> tuple[dict, dict]:
    """Run two stages and return (final_plan_dict, debug_info)."""
    debug: dict[str, Any] = {"stages": {}}

    # Stage A — narrative
    sysA = stage_a_system(niche)
    userA = stage_a_user(topic)
    payloadA = _stage_payload(settings.local_llm_model, sysA, userA, max_tokens=8000)
    tA0 = time.time()
    rawA = _post(settings, payloadA, timeout=timeout_s)
    debug["stages"]["A"] = {"time_s": round(time.time() - tA0, 1), "raw_chars": len(rawA)}
    debug["raw_A"] = rawA[:4000]
    partial = parse_stage_a(rawA)
    if not partial["full_script"] or len([c for c in partial["clauses"] if c["text"]]) < 14:
        n = len([c for c in partial["clauses"] if c["text"]])
        debug["stages"]["A"]["error"] = f"only {n} clauses parsed"
        e = RuntimeError(f"stage A: missing clauses (parsed {n}/14)")
        e.debug = debug
        raise e

    # Stage B — visual + motion + beat
    sysB = stage_b_system(niche)
    userB = stage_b_user(partial["clauses"])
    payloadB = _stage_payload(settings.local_llm_model, sysB, userB, max_tokens=8000)
    tB0 = time.time()
    rawB = _post(settings, payloadB, timeout=timeout_s)
    debug["stages"]["B"] = {"time_s": round(time.time() - tB0, 1), "raw_chars": len(rawB)}
    debug["raw_B"] = rawB[:4000]
    visuals = parse_stage_b(rawB)
    parsed_count = sum(1 for v in visuals if v["image_prompt"])
    debug["stages"]["B"]["parsed_count"] = parsed_count
    if parsed_count < 14:
        debug["stages"]["B"]["error"] = f"only {parsed_count}/14 visuals parsed"
        e = RuntimeError(f"stage B: missing visuals (parsed {parsed_count}/14)")
        e.debug = debug
        raise e

    # ── Merge ────────────────────────────────────────────────────────────────
    merged_clauses = []
    for c, v in zip(partial["clauses"], visuals):
        merged_clauses.append({
            "text": c["text"],
            "image_prompt": v["image_prompt"],
            "motion_prompt": v["motion_prompt"],
            "figure_present": v["figure_present"],
            "beat": v["beat"],
        })

    plan = {
        "historical_figure": partial["historical_figure"] or topic,
        "cold_open_object": partial["cold_open_object"],
        "decision_lever": partial["decision_lever"],
        "clauses": merged_clauses,
        "full_script": partial["full_script"],
        "lut_choice": partial["lut_choice"],
        "end_plate_question": partial["end_plate_question"],
    }

    validate_ctx = {"allow_figure_name": False, "niche": niche}
    validated = NarrationPlan.model_validate(plan, context=validate_ctx)
    return validated.model_dump(mode="json"), debug


__all__ = [
    "generate_multistage",
    "stage_a_system",
    "stage_b_system",
    "parse_stage_a",
    "parse_stage_b",
]
