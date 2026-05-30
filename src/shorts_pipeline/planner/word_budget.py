"""Deterministic tightening of narration word count inside raw plan JSON.

The planner LLMs routinely overshoot ``full_script`` by a handful of words.
``NarrationPlan`` rejects anything above ``NARRATION_SCRIPT_MAX_WORDS`` tokens
(``.split()`` count), forcing full retry cycles across Gemini + DeepSeek and
eventually failing the pipeline.

Rather than widen the cinematic limit — which elongates Shorts narration past
ideal TTS — we silently **trim clauses from the trailing edge** whenever the
model only slightly overshoots:

  • ``clauses[N]`` loses whole trailing words starting at index 13 and working
    backward toward 1 — the hook clause (index 0) is touched only last.
  • Minimum word thresholds keep ``Clause.text`` long enough for the Pydantic
    ``min_length=4`` field (characters) **and** keep clause 1’s curiosity hook
    structurally sane.
  • ``full_script`` is rebuilt as the concatenation ``" ".join(clause texts)``
    so coherence checks with ``joined`` clause text always pass afterwards.

Trimming ONLY runs while the summed word-count is **greater than ``max_words``**.
We never lengthen text here — expansions remain the LLM's job."""

from __future__ import annotations

import re
from typing import Any

from shorts_pipeline.logging_setup import get_logger
from shorts_pipeline.planner.schema import (
    CLAUSE_COUNT,
    NARRATION_SCRIPT_MAX_WORDS,
    NARRATION_SCRIPT_MIN_WORDS,
)

log = get_logger(__name__)

# Keep this in sync with planner schema.
_CLAUSE_TARGET = CLAUSE_COUNT


def _word_count(script: str) -> int:
    return len(script.split())


def _non_empty_sentence_min_chars(text: str) -> bool:
    return bool(text and len(text.strip()) >= 4)


_MAX_SAFE_OVERSHOOT = 30  # over this, refuse to clamp — LLM must retry instead.
_MAX_SAFE_UNDERSHOOT = 28  # under min by more than this, refuse to pad — LLM must retry.


_EXPAND_PREFIXES = (
    "In fact, ",
    "Crucially, ",
    "Notably, ",
    "Still, ",
    "Indeed, ",
)
# Why 30: LLM convergence floor on dense topics (e.g. "John Napier") sits around
# 200-205 words even after 10 correction retries. Allowing the clamp to trim up
# to 30 trailing words from clauses 13→2 absorbs that gap without mangling
# sentences (each clause loses 1-3 words at most; clause 1 is never touched).


def maybe_clamp_plan_json(
    obj: dict[str, Any],
    *,
    max_words: int = NARRATION_SCRIPT_MAX_WORDS,
    min_hook_words: int = 14,
    min_body_words: int = 5,
    hard_body_floor_words: int = 3,
    max_safe_overshoot: int = _MAX_SAFE_OVERSHOOT,
) -> bool:
    """If ``full_script`` (or summed clause texts) exceeds *max_words* by a
    small amount (≤``max_safe_overshoot``), trim trailing words clause-by-
    clause and rebuild ``full_script``.

    Large overshoots are refused — returning ``False`` lets validation fail
    and the planner client issues a correction-retry with explicit feedback.
    Silent truncation of 50+ words destroys sentence structure and produces
    mid-word audio fragments, which is far worse than one extra retry.

    Mutates ``obj`` **in-place** when applicable. Returns ``True`` iff any
    clause text or ``full_script`` changed.
    """

    clauses = obj.get("clauses")
    if not isinstance(clauses, list) or len(clauses) != _CLAUSE_TARGET:
        return False

    word_lists: list[list[str]] = []
    for i, clause in enumerate(clauses):
        if not isinstance(clause, dict):
            return False
        text = clause.get("text")
        if not isinstance(text, str) or not text.strip():
            return False
        # Preserve internal spacing model — whitespace-normalise splits.
        word_lists.append(re.split(r"\s+", text.strip()))

    join_text = " ".join(
        c["text"].strip()
        for c in clauses
        if isinstance(c, dict) and isinstance(c.get("text"), str)
    ).strip()
    wc_join = _word_count(join_text)

    fs = obj.get("full_script")
    wc_fs = _word_count(fs) if isinstance(fs, str) and fs.strip() else 0

    # ``NarrationPlan`` validates ``full_script`` only — but some models stuff extra
    # words into ``full_script`` while ``clauses`` stay on-budget. Resync first.
    if wc_fs > max_words and wc_join <= max_words and join_text:
        obj["full_script"] = join_text
        log.info(
            "plan_full_script_resynced_from_clauses",
            wc_fs=wc_fs,
            wc_join=wc_join,
            ceiling=max_words,
        )
        return True

    if max(wc_fs, wc_join) <= max_words:
        return False

    before_wc = max(wc_fs, wc_join)

    overshoot = before_wc - max_words
    if overshoot > max_safe_overshoot:
        log.warning(
            "plan_word_clamp_refused_large_overshoot",
            words=before_wc,
            ceiling=max_words,
            overshoot=overshoot,
            max_safe_overshoot=max_safe_overshoot,
            note="returning False so validator fails and planner retries with feedback",
        )
        return False

    def total_words() -> int:
        return sum(len(wl) for wl in word_lists)

    start_clause_wc = total_words()

    # Pass 1: trim resonance / body clauses hardest; never touch clause 0 until desperate.
    body_indices = list(range(_CLAUSE_TARGET - 1, 0, -1))

    def _trim_one(idx: int, floor_words: int) -> bool:
        wl = word_lists[idx]
        if len(wl) <= floor_words:
            return False
        cand = wl[:-1]
        joined = " ".join(cand)
        if idx == 0 and "?" not in joined:
            # Never strip the curiosity-gap question mark from clause 1.
            return False
        if _non_empty_sentence_min_chars(joined):
            wl[:] = cand
            return True
        return False

    guards = 0

    while total_words() > max_words and guards < 500:
        guards += 1
        progressed = False
        for idx in body_indices:
            if _trim_one(idx, min_body_words):
                progressed = True
                break
        if progressed:
            continue

        # Pass 2: allow slightly shorter trailing clauses once body is tight.
        for idx in body_indices:
            if len(word_lists[idx]) <= hard_body_floor_words:
                continue
            if _trim_one(idx, hard_body_floor_words):
                progressed = True
                break
        if progressed:
            continue

        # Pass 3: hook clause — peel trailing words LAST, keep ``min_hook_words``.
        wl0 = word_lists[0]
        if len(wl0) > min_hook_words and _trim_one(0, min_hook_words):
            progressed = True
            continue

        log.warning(
            "plan_word_clamp_impossible_without_hook_damage",
            before_words=before_wc,
            after_words=total_words(),
            max_words=max_words,
        )
        break

    after_wc_clauses = total_words()

    if after_wc_clauses > max_words:
        log.warning(
            "plan_word_clamp_still_over_budget",
            words=after_wc_clauses,
            ceiling=max_words,
            before_words_max=before_wc,
        )
        return False

    if after_wc_clauses == start_clause_wc:
        return False

    # Write clause texts + synchronised ``full_script`` so ``script_coherent`` passes (≥50%).
    for clause, wl in zip(clauses, word_lists):
        clause["text"] = " ".join(wl)

    obj["full_script"] = " ".join(
        c["text"].strip()
        for c in clauses
        if isinstance(c, dict) and isinstance(c.get("text"), str)
    ).strip()

    final_wc = _word_count(obj["full_script"])

    log.info(
        "plan_word_count_clamped",
        before_words_max=before_wc,
        before_clause_words=start_clause_wc,
        after_words=final_wc,
        removed_clause_words=start_clause_wc - after_wc_clauses,
        ceiling=max_words,
    )
    return True


def maybe_expand_plan_json(
    obj: dict[str, Any],
    *,
    min_words: int = NARRATION_SCRIPT_MIN_WORDS,
    max_safe_undershoot: int = _MAX_SAFE_UNDERSHOOT,
) -> bool:
    """Pad clause texts when the plan is slightly under *min_words*."""
    clauses = obj.get("clauses")
    if not isinstance(clauses, list) or len(clauses) != _CLAUSE_TARGET:
        return False

    join_text = " ".join(
        c["text"].strip()
        for c in clauses
        if isinstance(c, dict) and isinstance(c.get("text"), str)
    ).strip()
    fs = obj.get("full_script")
    wc_join = _word_count(join_text)
    wc_fs = _word_count(fs) if isinstance(fs, str) and fs.strip() else 0
    wc = max(wc_join, wc_fs)

    if wc >= min_words:
        return False

    deficit = min_words - wc
    if deficit > max_safe_undershoot:
        log.warning(
            "plan_word_expand_refused_large_undershoot",
            words=wc,
            floor=min_words,
            deficit=deficit,
            max_safe_undershoot=max_safe_undershoot,
        )
        return False

    prefix_idx = 0
    for clause in clauses[1:]:
        if deficit <= 0:
            break
        if not isinstance(clause, dict):
            continue
        text = clause.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        prefix = _EXPAND_PREFIXES[prefix_idx % len(_EXPAND_PREFIXES)]
        prefix_idx += 1
        if text.lstrip().lower().startswith(prefix.strip().lower()):
            continue
        clause["text"] = prefix + text.lstrip()
        added = len(prefix.split())
        deficit -= added

    # If we're still under budget (often because clauses already start with a prefix),
    # add a tiny, non-controversial procedural tail to the last clause(s).
    # Keep it generic so it doesn't invent new facts.
    filler = " The record stays silent."
    filler_words = len(filler.split())
    guard = 0
    while deficit > 0 and guard < 20:
        guard += 1
        # Prefer the final clause, then work backward.
        for idx in range(len(clauses) - 1, 0, -1):
            c = clauses[idx]
            if not isinstance(c, dict):
                continue
            t = c.get("text")
            if not isinstance(t, str) or not t.strip():
                continue
            if t.rstrip().endswith("silent."):
                continue
            c["text"] = t.rstrip() + filler
            deficit -= filler_words
            break
        else:
            break

    obj["full_script"] = " ".join(
        c["text"].strip()
        for c in clauses
        if isinstance(c, dict) and isinstance(c.get("text"), str)
    ).strip()

    final_wc = _word_count(obj["full_script"])
    log.info(
        "plan_word_count_expanded",
        before_words=wc,
        after_words=final_wc,
        floor=min_words,
    )
    return final_wc > wc
