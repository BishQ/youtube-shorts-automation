"""Shared planner helpers: JSON extraction and multi-turn validation prompts."""

from __future__ import annotations

import json
import re
from typing import Any, cast

from shorts_pipeline.planner.schema import (
    NARRATION_SCRIPT_MAX_WORDS,
    NARRATION_SCRIPT_MIN_WORDS,
)

_THINK_TAG_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
# Gemma 4 / some Ollama models emit reasoning between literal "Thinking..."
# and "...done thinking." markers instead of <think> tags.
_THINKING_BLOCK_RE = re.compile(
    r"Thinking\.\.\..*?\.\.\.done thinking\.",
    re.DOTALL | re.IGNORECASE,
)
# Trailing comma before a closing brace or bracket — invalid JSON, very common
# in LLM output especially on the last element of a long array.
_TRAILING_COMMA_RE = re.compile(r",\s*([\]}])")
# Single-line JS-style comments that some models inject: // ...
_JS_COMMENT_RE = re.compile(r"//[^\n]*")


def _repair_json(raw: str) -> str:
    """Best-effort cleanup of common LLM JSON generation artifacts."""
    # Strip JS-style comments before any other processing
    raw = _JS_COMMENT_RE.sub("", raw)
    # Remove trailing commas before ] or }
    # Apply twice to catch nested cases: [{...,},...,]
    for _ in range(3):
        raw = _TRAILING_COMMA_RE.sub(r"\1", raw)
    return raw


def _extract_json(raw: str) -> dict[str, Any]:
    """Parse JSON from raw model output, stripping surrounding noise."""
    raw = _THINK_TAG_RE.sub("", raw).strip()

    # First: try parsing as-is (fastest path for clean output)
    try:
        return cast(dict[str, Any], json.loads(raw))
    except json.JSONDecodeError:
        pass

    # Second: extract the outermost { ... } and retry
    start, end = raw.find("{"), raw.rfind("}")
    if start >= 0 and end > start:
        candidate = raw[start : end + 1]
        try:
            return cast(dict[str, Any], json.loads(candidate))
        except json.JSONDecodeError:
            pass
        # Third: apply repair heuristics and retry
        repaired = _repair_json(candidate)
        try:
            return cast(dict[str, Any], json.loads(repaired))
        except json.JSONDecodeError:
            pass

    # Final: repair the full raw string and retry extraction
    repaired_full = _repair_json(raw)
    start, end = repaired_full.find("{"), repaired_full.rfind("}")
    if start >= 0 and end > start:
        return cast(dict[str, Any], json.loads(repaired_full[start : end + 1]))

    raise json.JSONDecodeError("Could not extract valid JSON", raw, 0)


def _build_correction_message(obj: dict[str, Any], err: Exception) -> str:
    err_str = str(err)
    current_count = len(obj.get("clauses", []))

    if "too_short" in err_str and "clauses" in err_str:
        needed = 14 - current_count
        existing = "\n".join(
            f"  {i + 1}. {c.get('text', '')[:60]}"
            for i, c in enumerate(obj.get("clauses", []))
        )
        return (
            f"CRITICAL: You only wrote {current_count} clauses. The requirement is EXACTLY 14.\n"
            f"Your existing clauses:\n{existing}\n\n"
            f"You MUST add {needed} more clause(s) to reach exactly 14 total. "
            "Continue the story arc after the last clause above. "
            "Use structure: CLIMAX → RESONANCE for the remaining clauses. "
            "Each new clause needs its own image_prompt and beat metadata. "
            "Return the COMPLETE JSON with ALL 14 clauses (existing + new ones)."
        )

    if "too_long" in err_str and "clauses" in err_str:
        excess = current_count - 14
        return (
            f"Your JSON has {current_count} clauses. Maximum is 14. "
            f"Remove the {excess} weakest clause(s) and return the complete corrected JSON."
        )

    first_clause_hint = ""
    if "HERO PORTRAIT" in err_str or "FACE" in err_str:
        first_clause_hint = (
            "\n\nFirst-image fix: clauses[0].image_prompt MUST be a HERO PORTRAIT of the "
            "historical figure. Their FACE must be the dominant element — face filling the "
            "frame, eyes locked toward camera or sharp diagonal, expression legible. "
            "Required tokens: at least one of {face, eyes, jaw, brow, lips, mouth, profile, "
            "expression, gaze}. The cold_open_object may appear, but only as a SECONDARY "
            "element in the lower foreground or beside the hand. Hand-only, silhouette-only, "
            "or environment-only first images are REJECTED. Era-accurate clothing required.\n"
        )
    elif "clauses[0].image_prompt" in err_str or "establish the historical figure visually" in err_str:
        first_clause_hint = (
            "\n\nFirst-image fix: clauses[0].image_prompt must SHOW the historical figure "
            "in the frame — not the cold_open_object alone. Build a hero portrait shot:\n"
            "  - The figure's face fills a large portion of the frame.\n"
            "  - Eyes locked into the lens or sharp diagonal.\n"
            "  - Era-accurate clothing.\n"
            "  - An action moment (mid-grasp, mid-turn, mid-glance).\n"
            "  - The cold_open_object visible but secondary (beside the hand, lower foreground).\n"
        )

    hook_question_hint = ""
    if "curiosity-gap question" in err_str or "hook question" in err_str:
        hook_question_hint = (
            "\n\nHook-question fix: clauses[0].text MUST begin with a curiosity-gap question "
            "(How / Why / What / Who) of ≤14 words ending with '?'. The second sentence drops "
            "the viewer into the figure's world — never explains the question.\n"
            "Templates that work:\n"
            "  • 'How does a [vulnerable-version] become [final-form]?'\n"
            "  • 'Why would [respected group] kneel to [unlikely person]?'\n"
            "  • 'What does it cost to [seemingly-noble outcome]?'\n"
            "BANNED: yes/no questions, 'Did you know…', 'Who was…', generic openers.\n"
        )

    question_count_hint = ""
    if "question mark" in err_str.lower() and "clauses[1:]" in err_str:
        question_count_hint = (
            "\n\nQuestion-count fix: EXACTLY 2 question marks are allowed in the entire "
            "output — 1 in clauses[0].text (the hook) and 1 in end_plate_question. "
            "Remove every other '?' from the script. Convert mid-script questions into "
            "concrete statements with implied stakes.\n"
        )

    word_count_hint = ""
    err_low = err_str.lower()
    if "full_script" in err_low and "word" in err_low and "exceeds" in err_low:
        import re as _re

        wc_m = _re.search(r"full_script is (\d+)\s+words", err_str, _re.IGNORECASE)
        cap_m = _re.search(r"word\s+limit\s+of\s+(\d+)", err_str, _re.IGNORECASE)
        actual = wc_m.group(1) if wc_m else "?"
        cap = cap_m.group(1) if cap_m else str(NARRATION_SCRIPT_MAX_WORDS)
        try:
            must_remove = int(actual) - int(cap)
        except ValueError:
            must_remove = 0
        word_count_hint = (
            f"\n\nWord-count HARD FAIL (OVER LIMIT for this niche):\n"
            f"Your full_script is {actual} words; the niche-calibrated cap is {cap}. "
            f"Delete AT LEAST {max(1, must_remove)} whole tokens. Trim from clauses 12-14 "
            "(RESONANCE) first, then 8-11 (CLIMAX), never clause 1 (HOOK).\n"
            "Techniques that work:\n"
            "• Remove glue: that/just/even/really/very/basically.\n"
            "• Collapse duplicate ideas — ONE beat per clause.\n"
            "• Replace 4-syllable Latinate words with shorter equivalents.\n"
            "• Rebuild `full_script` as the literal join of all `clauses[i].text`.\n"
        )
    elif "full_script is only" in err_low and "minimum is" in err_low:
        import re as _re
        wc_m = _re.search(r"only (\d+) words", err_str, _re.IGNORECASE)
        min_m = _re.search(r"minimum is (\d+) words", err_str, _re.IGNORECASE)
        actual = wc_m.group(1) if wc_m else "?"
        floor = min_m.group(1) if min_m else str(NARRATION_SCRIPT_MIN_WORDS)
        word_count_hint = (
            f"\n\nWord-count fix: full_script has only {actual} tokens — MUST be at "
            f"least {floor}. Expand the CRISIS clauses (6-9) with ONE concrete verb + "
            "noun detail each. Count every token before submitting.\n"
        )

    syllable_hint = ""
    if "syllable" in err_low and "budget" in err_low:
        import re as _re
        sm = _re.search(r"contains (\d+) syllables", err_str)
        cm = _re.search(r"syllable\s+budget\s+of\s+(\d+)", err_str)
        actual = sm.group(1) if sm else "?"
        cap = cm.group(1) if cm else "?"
        syllable_hint = (
            f"\n\nSYLLABLE BUDGET HARD FAIL:\n"
            f"Your full_script is ~{actual} syllables; this niche's TTS budget is {cap}. "
            "Kokoro reads ~4.5 syllables/sec — you're over the 58 s body window. Word "
            "count alone does NOT predict TTS length; long Latinate words pack 2× the "
            "syllables of plain English equivalents. Rewrite the heaviest words:\n"
            "  • 'characteristics' (5 syl) → 'traits' (1 syl)\n"
            "  • 'demonstration'  (4 syl) → 'proof' (1 syl)\n"
            "  • 'logarithmic'    (4 syl) → 'log' (1 syl)\n"
            "  • 'extraordinarily'(6 syl) → 'incredibly' (4 syl) or 'wildly' (2 syl)\n"
            "  • 'investigation'  (5 syl) → 'probe' (1 syl)\n"
            "  • 'illustration'   (4 syl) → 'sketch' (1 syl)\n"
            "Scan clauses 8-14 first for the worst offenders. Recount syllables before "
            "submitting (vowel groups; 'ea' = 1).\n"
        )

    banned_phrase_hint = ""
    if "banned cliché phrases" in err_str:
        banned_phrase_hint = (
            "\n\nBanned-phrase fix: Replace every flagged phrase with a specific, concrete, "
            "historically-grounded image or fact. Examples:\n"
            "  • 'still echoes today' → name a specific modern institution, law, or word "
            "that survives.\n"
            "  • 'rivers of blood' → 'two thousand names crossed off a single scroll.'\n"
            "  • 'changed history' → describe the specific event with a concrete number.\n"
            "  • 'the legal document that changed everything' → name the document and its "
            "single most provocative clause.\n"
        )

    lighting_hint = ""
    if "image_prompt" in err_str and ("lighting" in err_str or "shot type" in err_str):
        lighting_hint = (
            "\n\nimage_prompt fix: each prompt must include a shot type "
            "(close-up, wide shot, medium shot, low-angle, high-angle, or establishing) "
            "AND explicit lighting words (not only style/film tags). "
            "Use at least one token from e.g.: dawn, dusk, harsh noon sun, overcast, "
            "torchlight, firelight, chiaroscuro, silhouette, moonlit, sunlit, "
            "dust haze, mist, glow, shadows, backlit, golden hour, blue hour, "
            "candlelight, rim light, cold blue light, amber flame-light. "
            "Phrases like 'reportage' or 'sharp grain' alone do not count as lighting.\n"
        )

    motion_hint = ""
    if "motion_prompt" in err_str:
        motion_hint = (
            "\n\nmotion_prompt fix: describe ONLY how the existing image MOVES — not the scene.\n"
            "Pattern: [camera move], [subject action], [atmosphere/dust/light shift]\n"
            "Required camera verb: push-in, pull-back, dolly, pan, tilt, orbit, tracks, "
            "zoom, static shot, or handheld.\n"
            "Match beat.camera:\n"
            "  ken_burns → camera slow push-in\n"
            "  pan → camera slow pan\n"
            "  zoom_out → camera slow pull-back\n"
            "  hold → camera static drift\n"
            "  parallax → camera slow orbit\n"
            "Example: 'camera slow push-in, subject turns toward lens, dust motes drift "
            "in warm light'\n"
        )

    return (
        "Your JSON failed schema validation. Fix ALL errors listed below:\n"
        f"{err_str}\n\n"
        "Exact valid values:\n"
        "• emotion: hook|tense_buildup|suspense|reveal|triumphant|tragic|climactic|reflective|shock\n"
        "• camera: ken_burns|pan|zoom_out|hold|parallax\n"
        "• audio_event: none|low_rumble|impact|paper_flutter\n"
        "• color_grade: epic_warm|tragic_cold|ancient_sepia|dark_thriller|golden_hour|null\n"
        "• image_prompt: must contain a shot type AND a lighting word\n"
        "• image_prompt BANNED: swastika/nazi flag/ss uniform, AND any graphic blood or gore\n"
        "  (blood-soaked, bleeding, severed, pool of blood, decapitation, dismemberment, corpse).\n"
        "  Imply violence through aftermath — torn cloak, fallen sword, smoke, distant collapsed figure.\n"
        f"{word_count_hint}"
        f"{syllable_hint}"
        f"{lighting_hint}"
        f"{motion_hint}"
        f"{first_clause_hint}"
        f"{hook_question_hint}"
        f"{question_count_hint}"
        f"{banned_phrase_hint}"
        "Return the complete corrected JSON object."
    )
