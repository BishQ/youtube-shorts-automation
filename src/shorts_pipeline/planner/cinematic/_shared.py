"""Shared helpers for all cinematic pipeline architectures.

Includes:
  - LLM POST helper that reads BOTH content and reasoning_content
  - Anti-AI phrase blacklist + remover
  - Latinate word swap dictionary
  - Multi-stage visual generator (Stage Z — same across all architectures)
  - Retry-with-feedback wrapper
  - Final plan validator + merger
"""

from __future__ import annotations

import re
import time
from typing import Any, Callable

import httpx
from pydantic import ValidationError

from shorts_pipeline.config.settings import Settings
from shorts_pipeline.planner.multistage import (
    parse_stage_a,
    parse_stage_b,
    stage_b_system,
    stage_b_user,
)
from shorts_pipeline.planner.schema import CLAUSE_COUNT, NarrationPlan


# ── Soft rules block — pasted into every Stage A prompt ──────────────────────

def hard_rules_block(min_w: int, max_w: int, max_syl: int) -> str:
    """Minimal rules. Engine validates afterwards, not the model.

    Lessons after the v3 review:
      - No syllable mentions (cognitive burden even when validator disabled).
      - No "self-verify" loops (cause partial output).
      - No multisyllabic instructions (cause bloated phrasing).
      - Target natural spoken pacing, not a count.
    """
    target = (min_w + max_w) // 2
    return f"""Target natural spoken pacing for ~58 seconds (~{target} words across {CLAUSE_COUNT} clauses).

OPENING — clause 1's first sentence is a curiosity question under 14 words.
  Don't name the subject in clauses 1-2. Reveal at clause 3 or 4.

CLOSING — END_QUESTION is one rhetorical question (ends with '?').

Mix short and long sentences for rhythm. Use plain Anglo-Saxon English."""


# ── Anti-AI phrase blacklist  (principle #5) ─────────────────────────────────

ANTI_AI_PATTERNS = [
    r"\bsuddenly\b",
    r"\blittle did (they|he|she|we|you) know\b",
    r"\bagainst all odds\b",
    r"\beverything changed\b",
    r"\bhistory would remember\b",
    r"\bbut then\b",
    r"\bin that moment\b",
    r"\bchanged history\b",
    r"\bshaped the world\b",
    r"\bstill echoes today\b",
    r"\brose to power\b",
    r"\band the rest is history\b",
    r"\bmind[- ]?blowing\b",
    r"\byou won'?t believe\b",
    r"\bscientists are baffled\b",
    r"\bin a stunning twist\b",
    r"\bwhat happened next\b",
    r"\bunbelievably\b",
]
_ANTI_AI_RE = re.compile("|".join(ANTI_AI_PATTERNS), re.IGNORECASE)


def contains_ai_phrases(text: str) -> list[str]:
    """Return the AI phrases found in `text`. Empty list = clean."""
    return list({m.group(0).lower() for m in _ANTI_AI_RE.finditer(text)})


def strip_ai_phrases(text: str) -> str:
    """Remove anti-AI phrases. Cheap mechanical cleanup."""
    return _ANTI_AI_RE.sub("", text).strip()


# ── Latinate → plain English swap dictionary  (principle #8) ─────────────────

LATINATE_SWAPS = {
    "characteristics": "traits",
    "demonstration": "proof",
    "demonstrate": "show",
    "investigation": "probe",
    "investigate": "probe",
    "revolutionary": "radical",
    "extraordinarily": "wildly",
    "logarithmic": "log",
    "illustration": "sketch",
    "infrastructure": "system",
    "interpretation": "read",
    "unprecedented": "new",
    "philosophical": "deep",
    "mathematical": "math",
    "additionally": "also",
    "furthermore": "and",
    "however": "but",
    "therefore": "so",
    "consequently": "so",
    "extensive": "wide",
    "comprehensive": "full",
    "subsequently": "later",
}


def apply_latinate_swaps(text: str) -> str:
    """Replace heavy Latinate words with plain-English equivalents."""
    out = text
    for heavy, light in LATINATE_SWAPS.items():
        # whole-word, case-insensitive
        out = re.sub(rf"\b{heavy}\b", light, out, flags=re.IGNORECASE)
    return out


# ── LLM POST helper ──────────────────────────────────────────────────────────

_THINK_RE = re.compile(r"<think(?:ing)?>.*?</think(?:ing)?>", re.DOTALL | re.IGNORECASE)


def llm_post(
    settings: Settings,
    *,
    system: str,
    user: str,
    max_tokens: int = 4000,
    temperature: float = 0.25,
    timeout_s: float = 600.0,
) -> str:
    """Send a chat completion and return the message content (with
    reasoning_content fallback for reasoning models).

    If ``settings.local_llm_base_url`` looks like a cloud API (api.deepseek.com,
    api.openai.com, etc.), look up the matching API-key env var and add the
    Authorization header. Local LM Studio / vLLM keeps working with no key.
    """
    import os
    base = settings.local_llm_base_url.rstrip("/")
    url = base + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    # Pick API key by base URL
    if "deepseek.com" in base:
        key = os.environ.get("DEEPSEEK_API_KEY")
        if key:
            headers["Authorization"] = f"Bearer {key}"
    elif "openai.com" in base:
        key = os.environ.get("OPENAI_API_KEY")
        if key:
            headers["Authorization"] = f"Bearer {key}"
    payload = {
        "model": settings.local_llm_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    with httpx.Client(timeout=timeout_s) as c:
        r = c.post(url, headers=headers, json=payload)
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
    msg = r.json()["choices"][0]["message"]
    content = (msg.get("content") or "").strip()
    if not content:
        content = (msg.get("reasoning_content") or "").strip()
    return _THINK_RE.sub("", content).strip()


# ── Python validator — runs BEFORE the LLM repair pass ──────────────────────

def find_narrative_issues(narrative: dict, min_w: int, max_w: int) -> list[str]:
    """Detect issues the LLM should be asked to fix. Returns a list of issues
    in plain English so the repair pass can act on them.

    Deliberately does NOT validate syllables — the LLM can't reliably count
    those. The downstream NarrationPlan validator will reject if needed.
    """
    issues: list[str] = []

    # 1. Word count
    full = narrative.get("full_script", "")
    if not full:
        full = " ".join(c.get("text", "") for c in narrative.get("clauses", []))
    w = len(full.split())
    if w < min_w:
        delta = min_w - w
        issues.append(
            f"Too short — full_script is {w} words, need at least {min_w}. "
            f"Add ~{delta} words by expanding 2-3 shortest clauses with named dates, "
            f"places, counts, or specific objects. Do NOT add new clauses."
        )
    elif w > max_w:
        delta = w - max_w
        issues.append(
            f"Too long — full_script is {w} words, max is {max_w}. "
            f"Trim ~{delta} words from longest clauses (drop adjectives, redundant phrases)."
        )

    # 2. Question marks — only allowed in clause 1 first sentence + END_QUESTION
    extra_qs = 0
    for i, c in enumerate(narrative.get("clauses", [])):
        text = c.get("text", "")
        if i == 0:
            # Clause 1 may have ONE '?' (the hook in the first sentence)
            if text.count("?") > 1:
                extra_qs += text.count("?") - 1
        else:
            extra_qs += text.count("?")
    if extra_qs > 0:
        issues.append(
            f"Too many question marks — {extra_qs} stray '?' inside clauses 2-{CLAUSE_COUNT}. "
            f"Rewrite those questions as statements (remove the '?')."
        )

    # 3. Identity reveal — name should not appear in clause 1 or 2
    name = (narrative.get("historical_figure") or "").strip()
    if name and len(name) >= 3:
        early = " ".join(narrative.get("clauses", [{}])[i].get("text", "")
                         for i in range(min(2, len(narrative.get("clauses", []))))).lower()
        if name.lower() in early:
            issues.append(
                f"Identity revealed too early — {name!r} appears in clause 1 or 2. "
                f"Replace those mentions with role/title (e.g. 'the regent', 'the king')."
            )

    return issues


def repair_narrative(
    settings: Settings,
    *,
    narrative: dict,
    issues: list[str],
    niche: str,
    timeout_s: float = 600.0,
) -> dict:
    """Ask the LLM to surgically fix specific issues without rewriting the whole
    plan. Keeps existing structure intact, only edits what needs editing.
    """
    if not issues:
        return narrative
    clauses_block = "\n".join(
        f"CLAUSE {i+1}: {c['text']}" for i, c in enumerate(narrative["clauses"])
    )
    issues_block = "\n".join(f"- {i}" for i in issues)
    sys_p = f"""You are doing a single targeted repair pass on a narration draft.

Fix ONLY the issues listed below. Do NOT rewrite clauses that are fine.
Keep the same number of clauses ({CLAUSE_COUNT}). Keep the same HISTORICAL_FIGURE,
COLD_OPEN_OBJECT, LEVER_*, LUT, END_QUESTION values from the draft.

ISSUES TO FIX:
{issues_block}

OUTPUT FORMAT — repeat the same 7 metadata lines, then CLAUSE 1..{CLAUSE_COUNT}:

HISTORICAL_FIGURE: <name>
COLD_OPEN_OBJECT: <object>
LEVER_TYPE: <law|geography|politics>
LEVER_DESC: <one sentence>
LEVER_CONSEQ: <one sentence>
LUT: <one of: epic_warm | tragic_cold | ancient_sepia | dark_thriller | golden_hour>
END_QUESTION: <one '?' question>

CLAUSE 1: <fixed text>
...
CLAUSE {CLAUSE_COUNT}: <fixed text>"""
    metadata = (
        f"HISTORICAL_FIGURE: {narrative.get('historical_figure','')}\n"
        f"COLD_OPEN_OBJECT: {narrative.get('cold_open_object','')}\n"
        f"LEVER_TYPE: {narrative.get('decision_lever',{}).get('lever_type','politics')}\n"
        f"LEVER_DESC: {narrative.get('decision_lever',{}).get('description','')}\n"
        f"LEVER_CONSEQ: {narrative.get('decision_lever',{}).get('consequence','')}\n"
        f"LUT: {narrative.get('lut_choice','epic_warm')}\n"
        f"END_QUESTION: {narrative.get('end_plate_question','')}"
    )
    user_p = f"DRAFT TO REPAIR:\n\n{metadata}\n\n{clauses_block}\n\nApply the fixes."
    raw = llm_post(settings, system=sys_p, user=user_p,
                   max_tokens=8000, timeout_s=timeout_s, temperature=0.2)
    from shorts_pipeline.planner.multistage import parse_stage_a
    repaired = parse_stage_a(raw)
    parsed = sum(1 for c in repaired["clauses"] if c["text"])
    if parsed < CLAUSE_COUNT:
        # repair failed — return original
        return narrative
    # Preserve fields if repair dropped them
    for key in ("historical_figure", "cold_open_object", "lut_choice", "end_plate_question"):
        if not repaired.get(key):
            repaired[key] = narrative.get(key, "")
    if not repaired.get("decision_lever", {}).get("description"):
        repaired["decision_lever"] = narrative.get("decision_lever", {})
    return repaired


# ── Auto-fix stray question marks in body clauses ─────────────────────────────

def strip_stray_questions(narrative: dict) -> dict:
    """Replace '?' with '.' in clauses 2..N, and any second '?' in clause 1.

    The validator allows exactly ONE '?' in clause 1's first sentence and one
    END_QUESTION. Anything else is converted to a period silently.
    """
    clauses = narrative.get("clauses", [])
    for i, c in enumerate(clauses):
        text = c.get("text", "")
        if not text:
            continue
        if i == 0:
            # Keep ONE '?' (first one), convert any later ones in clause 1.
            first_q = text.find("?")
            if first_q < 0:
                # No '?' at all — leave alone, validator will handle if mandatory.
                continue
            head = text[:first_q + 1]
            tail = text[first_q + 1:].replace("?", ".")
            clauses[i]["text"] = head + tail
        else:
            # No questions allowed in body clauses.
            if "?" in text:
                clauses[i]["text"] = text.replace("?", ".")
    narrative["full_script"] = " ".join(c.get("text", "") for c in clauses).strip()
    return narrative


# ── Cadence rewriter — break uniform sentence pacing  ───────────────────────

def cadence_rewrite(
    settings: Settings,
    *,
    narrative: dict,
    niche: str,
    timeout_s: float = 600.0,
) -> dict:
    """Rewrite narration clauses to introduce rhythm — short hits, long lines,
    deliberate pauses. Only triggers if pacing is uniform (most clauses within
    ±2 words of each other).
    """
    clauses = narrative.get("clauses", [])
    if len(clauses) != CLAUSE_COUNT:
        return narrative

    # Skip if narrative is already at or above the niche band — rewriting risks
    # blowing the upper bound. Cadence is a quality nudge, not a length lever.
    from shorts_pipeline.planner.niche_caps import caps_for
    min_w, max_w, _ = caps_for(niche)
    full = narrative.get("full_script") or " ".join(c.get("text", "") for c in clauses)
    word_count = len(full.split())
    if word_count >= min_w:
        return narrative  # Already long enough — leave alone, validator will check

    lens = [len(c["text"].split()) for c in clauses]
    from statistics import stdev
    if len(lens) < 2 or stdev(lens) >= 3.0:
        return narrative  # Already varied enough

    # Ask LLM to rewrite for rhythm. Keep meaning, break pacing.
    clauses_block = "\n".join(
        f"CLAUSE {i+1}: {c['text']}" for i, c in enumerate(clauses)
    )
    sys_p = f"""You are rewriting a {CLAUSE_COUNT}-clause Short narration for spoken RHYTHM.

The current draft is too uniform — every clause is the same length.
Real cinematic narration alternates: short hit, medium explain, short punch,
long cinematic line, very short impact.

Example transformation:
  BEFORE (uniform):
    "The army was massive. The hero stood alone. He drew his sword. He charged."
  AFTER (rhythmic):
    "The army covered the horizon. He stood alone. Then he drew his sword —
     not because he expected to win, but because he was already counted dead.
     He charged."

RULES:
- Keep all {CLAUSE_COUNT} clauses.
- Keep all factual content.
- Change LENGTHS only — make some 4-5 words, some 18-22 words.
- Pattern target: short / medium / short / long / impact / medium / short / etc.
- Keep clause 1's question and the final clause's setup.

Output same labelled format as input."""
    user_p = f"REWRITE FOR RHYTHM:\n\n{clauses_block}"
    raw = llm_post(settings, system=sys_p, user=user_p,
                   max_tokens=1500, timeout_s=timeout_s, temperature=0.5)
    from shorts_pipeline.planner.multistage import parse_stage_a
    revised = parse_stage_a(raw)
    if len([c for c in revised["clauses"] if c["text"]]) < CLAUSE_COUNT:
        return narrative  # rewrite failed, keep original
    # Preserve metadata
    revised["historical_figure"] = narrative.get("historical_figure", revised.get("historical_figure", ""))
    revised["cold_open_object"] = narrative.get("cold_open_object", revised.get("cold_open_object", ""))
    revised["decision_lever"] = narrative.get("decision_lever", revised.get("decision_lever", {}))
    revised["lut_choice"] = narrative.get("lut_choice", revised.get("lut_choice", "epic_warm"))
    revised["end_plate_question"] = narrative.get("end_plate_question", revised.get("end_plate_question", ""))
    return revised


# ── Stage Z — Visual generator (shared across all architectures) ─────────────

def visual_stage(
    settings: Settings,
    *,
    niche: str,
    clauses: list[dict],
    timeout_s: float = 600.0,
    previous_error: str | None = None,
) -> list[dict]:
    """Generate visual blocks for all clauses. Returns parsed list."""
    sys_p = stage_b_system(niche)
    if previous_error:
        sys_p += f"\n\nIMPORTANT — your last attempt failed with: {previous_error}\nFix this and try again."
    user_p = stage_b_user(clauses)
    raw = llm_post(settings, system=sys_p, user=user_p,
                   max_tokens=16000, timeout_s=timeout_s)
    visuals = parse_stage_b(raw)
    parsed = sum(1 for v in visuals if v["image_prompt"])
    if parsed < CLAUSE_COUNT:
        raise RuntimeError(f"visual stage parsed {parsed}/{CLAUSE_COUNT} blocks")
    return visuals


# ── Validation + merge ──────────────────────────────────────────────────────

def merge_and_validate(
    *,
    narrative: dict,
    visuals: list[dict],
    niche: str,
    topic: str,
) -> dict:
    """Merge narrative (Stage A output) + visuals (Stage B) into a NarrationPlan,
    run pydantic validation, and return the validated plan dict."""
    merged = []
    for c, v in zip(narrative["clauses"], visuals):
        merged.append({
            "text": c["text"],
            "image_prompt": v["image_prompt"],
            "motion_prompt": v["motion_prompt"],
            "figure_present": v.get("figure_present", True),
            "beat": v["beat"],
        })
    plan = {
        "historical_figure": narrative.get("historical_figure") or topic,
        "cold_open_object": narrative.get("cold_open_object") or "an unnamed object",
        "decision_lever": narrative.get("decision_lever") or {
            "lever_type": "politics", "description": "", "consequence": ""},
        "clauses": merged,
        "full_script": narrative.get("full_script") or
                       " ".join(c["text"] for c in narrative["clauses"]),
        "lut_choice": narrative.get("lut_choice") or "epic_warm",
        "end_plate_question": narrative.get("end_plate_question") or "",
    }
    validated = NarrationPlan.model_validate(
        plan, context={"allow_figure_name": True, "niche": niche})
    return validated.model_dump(mode="json")


# ── Retry-with-feedback wrapper ──────────────────────────────────────────────

def with_retries(
    pipeline_fn: Callable[..., dict],
    *,
    settings: Settings,
    niche: str,
    topic: str,
    max_retries: int = 3,
    timeout_s: float = 600.0,
) -> tuple[dict, dict]:
    """Run pipeline_fn up to max_retries times, feeding each error back as
    `previous_error` so the model can self-correct.

    pipeline_fn must accept (settings, niche=..., topic=..., timeout_s=...,
        previous_error=...) and return (plan_dict, stage_debug).
    """
    attempts: list[dict] = []
    last_err: Exception | None = None
    feedback: str | None = None
    for attempt in range(1, max_retries + 1):
        t0 = time.time()
        try:
            plan, stage_debug = pipeline_fn(
                settings, niche=niche, topic=topic,
                timeout_s=timeout_s, previous_error=feedback,
            )
            attempts.append({
                "n": attempt, "time_s": round(time.time() - t0, 1),
                "success": True, "stages": stage_debug,
            })
            return plan, {"attempts": attempts, "succeeded_on": attempt}
        except ValidationError as e:
            feedback = _summarise_validation_error(e)
            last_err = e
            attempts.append({
                "n": attempt, "time_s": round(time.time() - t0, 1),
                "success": False, "feedback": feedback,
            })
        except RuntimeError as e:
            feedback = str(e)[:300]
            last_err = e
            attempts.append({
                "n": attempt, "time_s": round(time.time() - t0, 1),
                "success": False, "feedback": feedback,
            })
    # All retries exhausted
    final = RuntimeError(
        f"all {max_retries} attempts failed; last error: {feedback}")
    final.debug = {"attempts": attempts, "last_error": str(last_err)[:500]}
    raise final


def _summarise_validation_error(e: ValidationError) -> str:
    """Compact human-readable summary of pydantic validation errors."""
    parts = []
    for err in e.errors()[:5]:
        loc = ".".join(str(x) for x in err.get("loc", []))
        msg = err.get("msg", "")[:120]
        parts.append(f"{loc}: {msg}")
    return " | ".join(parts)[:500]
