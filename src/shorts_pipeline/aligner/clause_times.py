"""Clause boundary times derived from word spans."""

from __future__ import annotations

import re

from shorts_pipeline.aligner.base import WordSpan
from shorts_pipeline.planner.schema import NarrationPlan


def _split_tokens(text: str) -> list[str]:
    """Space-split, filtering empties.  Matches _snap_spans_to_reference tokenisation."""
    return [t for t in text.split() if t.strip()]


def _norm_tok(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", t.lower())


def clause_word_offsets(plan: NarrationPlan) -> list[tuple[int, int]]:
    """Return (start_idx, end_idx) for each clause in the full_script token list.

    Both sides are space-split so token counts match _snap_spans_to_reference output.
    Greedy forward match: each clause token is searched within a small window of the
    current position in full_script, tolerating minor wording differences.
    """
    full_toks = _split_tokens(plan.full_script)
    full_norms = [_norm_tok(t) for t in full_toks]
    offsets: list[tuple[int, int]] = []
    pos = 0
    for clause in plan.clauses:
        clause_norms = [_norm_tok(w) for w in _split_tokens(clause.text)]
        clause_norms = [c for c in clause_norms if c]  # drop pure-punctuation tokens
        if not clause_norms:
            offsets.append((pos, pos))
            continue
        start = pos
        j = pos
        for ct in clause_norms:
            for k in range(j, min(j + 6, len(full_norms))):
                ft = full_norms[k]
                if ft and (ft == ct or ft.startswith(ct) or ct.startswith(ft)):
                    j = k + 1
                    break
        offsets.append((start, j))
        pos = j
    return offsets


def ensure_word_spans_for_plan(plan: NarrationPlan, words: list[WordSpan]) -> list[WordSpan]:
    """Pad/truncate word spans to match full_script token count (space-split)."""
    full_toks = _split_tokens(plan.full_script)
    if not full_toks:
        raise ValueError("empty full_script")
    w = list(words)
    if len(w) > len(full_toks):
        w = w[: len(full_toks)]
    if len(w) < len(full_toks):
        last_end = w[-1].end_s if w else 0.0
        while len(w) < len(full_toks):
            i = len(w)
            last_end += 0.02
            w.append(WordSpan(word=full_toks[i], start_s=last_end - 0.02, end_s=last_end))
    return w


def clause_time_ranges_from_words(plan: NarrationPlan, words: list[WordSpan]) -> list[tuple[float, float]]:
    """For each clause, take time range from its word span in full_script."""
    words = ensure_word_spans_for_plan(plan, words)
    offsets = clause_word_offsets(plan)
    ranges: list[tuple[float, float]] = []
    for start_idx, end_idx in offsets:
        chunk = words[start_idx:end_idx]
        if chunk:
            ranges.append((chunk[0].start_s, chunk[-1].end_s))
        else:
            prev_end = ranges[-1][1] if ranges else 0.0
            ranges.append((prev_end, prev_end + 0.05))
    return ranges
