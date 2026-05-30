"""Compute per-clause clip durations from word alignments or precomputed ranges.

Timing law (Shorts, 11 clauses → 11 images/clips):

* **Hook (clip 0)** is held ``HOOK_MIN_DURATION_S``–``HOOK_MAX_DURATION_S`` so the
  curiosity gap lands before the first cut.
* **Body (clips 1..N-1)** follow their own TTS length but are clamped to
  ``BODY_MIN_DURATION_S``–``BODY_MAX_DURATION_S``.
* Clips always **tile ``[0, narration_duration_s]`` exactly** — the last clip
  ends at the narration end, never parks on a frozen frame.

The previous implementation nudged cut *starts* with a single [min, max] band.
When the narration was longer than ``N × max`` it could not satisfy the band and
dumped all the leftover seconds onto the final clip (the "last image frozen 12 s"
bug). This version works in the **duration domain**: every clip is sized inside
its band, then the residual needed to hit ``narration_duration_s`` is spread
*proportionally* across the clips that still have room. If the narration is
longer than the whole deck can tile at its maxima, the overflow is shared evenly
across the body clips instead of being parked on one image.
"""

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


# ── Per-clip duration law ──────────────────────────────────────────────────────
# Hook gets a long, confident hold so the curiosity gap reads before the cut.
HOOK_MIN_DURATION_S = 6.5
HOOK_MAX_DURATION_S = 7.5
# Every other scene rides its TTS length, clamped to this tight cinematic band.
BODY_MIN_DURATION_S = 3.7
BODY_MAX_DURATION_S = 5.0

# Back-compat aliases. Older callers/tests import these names; they now map to the
# BODY band (the hook carries its own wider bounds above). ``compensate_xfade_overlap``
# in renderer/ffmpeg.py uses ``MAX_CLIP_DURATION_S`` as its per-image stretch ceiling.
MIN_CLIP_DURATION_S = BODY_MIN_DURATION_S
MAX_CLIP_DURATION_S = BODY_MAX_DURATION_S

# Deck tiling envelope for the production 11-image Short:
#   sum_min = 6.5 + 10 × 3.7 = 43.5 s
#   sum_max = 7.5 + 10 × 5.0 = 57.5 s
# Narration is budgeted to ~54–57 s (see planner.niche_caps); + the ~2 s end plate
# this lands the FINAL video in the 57–59.5 s target window under the 60 s hard cap
# (render_max_shorts_duration_s). Narration inside [43.5, 57.5] keeps every clip
# in-band; beyond 57.5 s the overflow is shared evenly across body clips rather
# than parked on the last image (the old "frozen 12 s last frame" bug).


def _clip_bounds(n: int) -> tuple[list[float], list[float]]:
    """Per-clip (lower, upper) duration bounds: clip 0 = hook band, rest = body band."""
    lo: list[float] = []
    hi: list[float] = []
    for i in range(n):
        if i == 0 and n > 1:
            lo.append(HOOK_MIN_DURATION_S)
            hi.append(HOOK_MAX_DURATION_S)
        else:
            lo.append(BODY_MIN_DURATION_S)
            hi.append(BODY_MAX_DURATION_S)
    return lo, hi


def _fit_durations(
    d: list[float],
    lo: list[float],
    hi: list[float],
    target: float,
    body_first_idx: int,
) -> list[float]:
    """Scale ``d`` so it sums to ``target`` while respecting per-clip [lo, hi].

    The residual (target − sum) is distributed *proportionally* to each clip's
    remaining headroom (when growing) or slack above its floor (when shrinking),
    then re-clamped, iterating until it converges. When every clip is already
    saturated at its bound the leftover is split evenly across the body clips so
    no single image absorbs the whole overflow.
    """
    n = len(d)
    if n == 0:
        return d
    body = list(range(body_first_idx, n)) or list(range(n))

    for _ in range(400):
        diff = target - sum(d)
        if abs(diff) <= 1e-7:
            break
        if diff > 0:
            room = [hi[i] - d[i] for i in range(n)]
            total = sum(r for r in room if r > 0)
            if total <= 1e-9:
                add = diff / len(body)
                for i in body:
                    d[i] += add
                break
            for i in range(n):
                if room[i] > 0:
                    d[i] += diff * room[i] / total
        else:
            slack = [d[i] - lo[i] for i in range(n)]
            total = sum(s for s in slack if s > 0)
            if total <= 1e-9:
                sub = (-diff) / len(body)
                for i in body:
                    d[i] = max(0.05, d[i] - sub)
                break
            for i in range(n):
                if slack[i] > 0:
                    d[i] += diff * slack[i] / total
        for i in range(n):
            d[i] = min(hi[i], max(lo[i], d[i]))
    return d


def _normalize_ranges(
    ranges: list[tuple[float, float]],
    narration_duration_s: float,
) -> list[tuple[float, float]]:
    """Size each clip inside its band and tile ``[0, narration_duration_s]`` exactly.

    The incoming ``ranges`` carry the TTS-derived clause timing; their per-clause
    *length* sets the target each clip aims for before clamping. The cuts are then
    laid back-to-back so the deck never parks on a frozen final frame.
    """
    if not ranges:
        return []
    narr = float(narration_duration_s)
    n = len(ranges)

    if n == 1:
        # Single clip must cover the whole narration; bound only the floor.
        end = max(narr, BODY_MIN_DURATION_S)
        return [(0.0, max(0.05, end))]

    # Natural (TTS) length each clause wants, from its start to the next start.
    starts_in = [float(r[0]) for r in ranges]
    tts = []
    for i in range(n):
        nxt = starts_in[i + 1] if i + 1 < n else narr
        tts.append(max(0.0, nxt - starts_in[i]))

    lo, hi = _clip_bounds(n)
    d = [min(hi[i], max(lo[i], tts[i])) for i in range(n)]
    d = _fit_durations(d, lo, hi, narr, body_first_idx=1)

    # Lay cuts back-to-back; force the last clip to end exactly at narr.
    result: list[tuple[float, float]] = []
    cursor = 0.0
    for i in range(n):
        start = cursor
        end = narr if i == n - 1 else cursor + d[i]
        if end < start + 0.05:
            end = start + 0.05
        result.append((start, end))
        cursor = end
    return result


def compute_i2v_generation_durations_s(
    ranges: list[tuple[float, float]],
    narration_duration_s: float,
    *,
    margin_s: float = 0.25,
    max_clip_s: float = 7.5,
    last_clip_extra_s: float = 0.0,
) -> list[float]:
    """I2V source length per clause from the render pacing graph + trim headroom.

    FFmpeg trims each generated MP4 to the normalized slot (``end - start`` from
    ``compute_cut_times_from_ranges``). We synthesize ``slot + margin_s`` (capped
    at ``max_clip_s``) so RunPod does not waste GPU on uniform ~6 s body clips
    while still leaving enough frames for trim and the last-clip breathe pad.
    """
    normalized = compute_cut_times_from_ranges(ranges, narration_duration_s)
    if not normalized:
        return []
    n = len(normalized)
    out: list[float] = []
    for i, (start, end) in enumerate(normalized):
        slot = max(0.05, float(end) - float(start))
        extra = last_clip_extra_s if i == n - 1 else 0.0
        gen = min(float(max_clip_s), slot + float(margin_s) + extra)
        out.append(max(0.5, gen))
    return out
