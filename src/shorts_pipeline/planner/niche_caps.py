"""Per-niche TTS word/syllable caps targeting a 56.5–59.5 s final video.

Target derivation:
  • Final video window: 56.5–59.5 s (Shorts).
  • Final = narration + outro (~2.0 s) + tail_pad (~0.5 s).
  • Therefore narration target: 54.0–57.0 s.
  • Empirical Kokoro `am_adam` rate: ~5.2 syllables/sec (measured from a
    history run: 148 words × 1.73 syl/word = 256 syl → 49.4 s).
  • Syllable target window: 281–296 syllables (54.0–57.0 s × 5.2).

Per-niche word ranges fall out of the syllable target divided by the niche's
average syllables-per-word — dense niches (science, history) need fewer
words to hit the same duration; light niches (mythology, wealth) need more.
The validator rejects scripts outside ``[min_words, max_words]`` AND outside
``[min_syllables, max_syllables]``, so under-budget scripts retry until the
LLM lands inside the window.
"""

from __future__ import annotations

# Defaults are intentionally wide — an unknown niche still has to clear the
# syllable gate, but its word range can vary depending on vocabulary density.
DEFAULT_MIN_WORDS = 160
DEFAULT_MAX_WORDS = 210
DEFAULT_MIN_SYLLABLES = 281
DEFAULT_MAX_SYLLABLES = 296
# Legacy export — some callers still import ``MIN_WORDS``.
MIN_WORDS = DEFAULT_MIN_WORDS

# Each row targets the same 281–296 syllable window (≈54–57 s Kokoro).
# avg_syl_per_word was recomputed from the few-shot examples themselves (the
# real signal the LLM imitates) — the prior table was calibrated against
# broken data (most rows were 2–3 s aborted TTS runs, not full narrations).
# Word range = (syl_window / measured_spw) ± 7 word wiggle. The HARD gate is
# the syllable budget; word range is the cheap proxy for prompt guidance.
# Word ranges reduced 15% — May 2026 recalibration after audio came out 70-90s
# instead of target 54-57s. Combined with kokoro_speed=1.2x this lands audio
# back in the 50-58s Shorts window.
NICHE_CAPS: dict[str, dict[str, int | float]] = {
    "business":     {"min_words": 138, "max_words": 158, "min_syllables": 239, "max_syllables": 252, "avg_syl_per_word": 1.67},
    "cosmic":       {"min_words": 143, "max_words": 162, "min_syllables": 239, "max_syllables": 252, "avg_syl_per_word": 1.63},
    # Crime scripts often need a few extra procedural beats to stay grounded.
    # Keep the upper bound aligned with the global default to avoid planner deadlocks.
    "crime":        {"min_words": 160, "max_words": 210, "min_syllables": 239, "max_syllables": 252, "avg_syl_per_word": 1.53},
    "cults":        {"min_words": 139, "max_words": 158, "min_syllables": 239, "max_syllables": 252, "avg_syl_per_word": 1.66},
    "documentary":  {"min_words": 153, "max_words": 173, "min_syllables": 239, "max_syllables": 252, "avg_syl_per_word": 1.50},
    "edutainment":  {"min_words": 152, "max_words": 172, "min_syllables": 239, "max_syllables": 252, "avg_syl_per_word": 1.51},
    "health":       {"min_words": 148, "max_words": 168, "min_syllables": 239, "max_syllables": 252, "avg_syl_per_word": 1.55},
    "history":      {"min_words": 153, "max_words": 173, "min_syllables": 239, "max_syllables": 252, "avg_syl_per_word": 1.50},
    "lost_tech":    {"min_words": 148, "max_words": 168, "min_syllables": 239, "max_syllables": 252, "avg_syl_per_word": 1.55},
    "military":     {"min_words": 145, "max_words": 164, "min_syllables": 239, "max_syllables": 252, "avg_syl_per_word": 1.59},
    "mythology":    {"min_words": 175, "max_words": 196, "min_syllables": 239, "max_syllables": 252, "avg_syl_per_word": 1.32},
    "psychology":   {"min_words": 159, "max_words": 179, "min_syllables": 239, "max_syllables": 252, "avg_syl_per_word": 1.45},
    "science":      {"min_words": 139, "max_words": 159, "min_syllables": 239, "max_syllables": 252, "avg_syl_per_word": 1.64},
    "sports":       {"min_words": 162, "max_words": 184, "min_syllables": 239, "max_syllables": 252, "avg_syl_per_word": 1.42},
    "survival":     {"min_words": 156, "max_words": 176, "min_syllables": 239, "max_syllables": 252, "avg_syl_per_word": 1.48},
    "tech_hackers": {"min_words": 139, "max_words": 158, "min_syllables": 239, "max_syllables": 252, "avg_syl_per_word": 1.65},
    "wealth":       {"min_words": 133, "max_words": 153, "min_syllables": 239, "max_syllables": 252, "avg_syl_per_word": 1.71},
}


def caps_for(niche: str | None) -> tuple[int, int, int]:
    """Return ``(min_words, max_words, max_syllables)`` for the given niche.

    Unknown / missing niches receive the conservative defaults so we never
    silently expand the budget for a misspelled or new niche.
    """
    if not niche:
        return DEFAULT_MIN_WORDS, DEFAULT_MAX_WORDS, DEFAULT_MAX_SYLLABLES
    n = NICHE_CAPS.get(niche)
    if n is None:
        return DEFAULT_MIN_WORDS, DEFAULT_MAX_WORDS, DEFAULT_MAX_SYLLABLES
    return int(n["min_words"]), int(n["max_words"]), int(n["max_syllables"])


def syllable_floor_for(niche: str | None) -> int:
    """Return the per-niche ``min_syllables`` floor (54 s narration target)."""
    if not niche:
        return DEFAULT_MIN_SYLLABLES
    n = NICHE_CAPS.get(niche)
    if n is None:
        return DEFAULT_MIN_SYLLABLES
    return int(n["min_syllables"])


def count_syllables(text: str) -> int:
    """Cheap vowel-group syllable estimate. Matches the calibration script."""
    import re as _re

    vowels = _re.compile(r"[aeiouyAEIOUY]+")
    word_re = _re.compile(r"[A-Za-z][A-Za-z'-]*")
    total = 0
    for w in word_re.findall(text):
        w = w.strip("-'")
        if not w:
            continue
        total += max(1, len(vowels.findall(w)))
    return total
