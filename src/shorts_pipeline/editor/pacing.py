"""Compute per-clause cut times from word alignments or precomputed ranges."""

from __future__ import annotations

from shorts_pipeline.aligner.base import WordSpan
from shorts_pipeline.aligner.clause_times import clause_time_ranges_from_words
from shorts_pipeline.planner.schema import NarrationPlan


def compute_cut_times_from_words(
    plan: NarrationPlan,
    words: list[WordSpan],
    narration_duration_s: float,
) -> list[tuple[float, float]]:
    """Return (start_s, end_s) per clause aligned to first word of each clause (Rule 3)."""
    ranges = clause_time_ranges_from_words(plan, words)
    return _normalize_ranges(ranges, narration_duration_s)


def compute_cut_times_from_ranges(
    ranges: list[tuple[float, float]],
    narration_duration_s: float,
) -> list[tuple[float, float]]:
    """Return (start_s, end_s) per clause from precomputed time ranges."""
    return _normalize_ranges(list(ranges), narration_duration_s)


# Minimum time each image stays on screen (Ken Burns clip). Alignment can pack
# many clause starts into a few seconds; we nudge cut times so each clip is at
# least this wide when ``narration_duration_s`` allows (may overlap word timing).
MIN_CLIP_DURATION_S = 2.5

# Upper bound per image so a long TTS / silence tail does not park one frame
# for many seconds while audio finishes (clips still pad to full narr in mux).
MAX_CLIP_DURATION_S = 4.5


def _clamp_starts_for_min_max(
    starts: list[float], narr: float, mind: float, maxd: float
) -> list[float]:
    """Adjust cut starts so every clip length is in [mind, maxd] where feasible."""
    n = len(starts)
    if n == 0:
        return starts
    s = [float(x) for x in starts]
    if maxd < mind:
        return s
    for _ in range(n + 24):
        changed = False
        for i in range(n - 1):
            lo, hi = s[i] + mind, s[i] + maxd
            if s[i + 1] < lo - 1e-9:
                s[i + 1] = lo
                changed = True
            elif s[i + 1] > hi + 1e-9:
                s[i + 1] = hi
                changed = True
        lo_last, hi_last = narr - maxd, narr - mind
        if s[-1] < lo_last - 1e-9:
            s[-1] = lo_last
            changed = True
        elif s[-1] > hi_last + 1e-9:
            s[-1] = hi_last
            changed = True
        for i in range(n - 2, -1, -1):
            lo, hi = s[i + 1] - maxd, s[i + 1] - mind
            if s[i] < lo - 1e-9:
                s[i] = lo
                changed = True
            elif s[i] > hi + 1e-9:
                s[i] = hi
                changed = True
        if s[0] < 0.0:
            s[0] = 0.0
            changed = True
        if not changed:
            break
    return s


def _normalize_ranges(
    ranges: list[tuple[float, float]],
    narration_duration_s: float,
) -> list[tuple[float, float]]:
    """
    Recompute end_s so each clip ends where the next begins (cut on word boundary).
    The last clip ends at narration_duration_s.

    When Whisper/plan boundaries collapse clauses into very short slices, we nudge
    ``starts`` so every clip is at least ``MIN_CLIP_DURATION_S`` seconds wide
    (as far as ``narration_duration_s`` allows), pushing cuts earlier in time.
    """
    if not ranges:
        return []
    narr = float(narration_duration_s)
    starts = [float(r[0]) for r in ranges]
    n = len(starts)
    mind = MIN_CLIP_DURATION_S
    maxd = MAX_CLIP_DURATION_S

    if n == 1:
        s0 = starts[0]
        if narr - s0 < mind:
            s0 = max(0.0, narr - mind)
        if narr - s0 > maxd:
            s0 = max(0.0, narr - maxd)
        return [(s0, max(s0 + 0.05, narr))]

    for i in range(1, n):
        if starts[i] < starts[i - 1] + 0.02:
            starts[i] = starts[i - 1] + 0.02

    for _ in range(n + 8):
        changed = False
        for i in range(n - 1):
            if starts[i + 1] - starts[i] < mind:
                starts[i + 1] = starts[i] + mind
                changed = True
        if narr - starts[-1] < mind:
            starts[-1] = narr - mind
            changed = True
        for i in range(n - 2, -1, -1):
            if starts[i + 1] - starts[i] < mind:
                starts[i] = starts[i + 1] - mind
                changed = True
        if starts[0] < 0.0:
            starts[0] = 0.0
            changed = True
        if not changed:
            break

    if starts[-1] >= narr:
        starts[-1] = max(0.0, narr - 0.05)

    starts = _clamp_starts_for_min_max(starts, narr, mind, maxd)

    result: list[tuple[float, float]] = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < n else narr
        end = min(end, narr)
        result.append((start, max(start + 0.05, end)))
    return result
