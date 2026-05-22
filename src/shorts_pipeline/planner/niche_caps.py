"""Per-niche TTS word/syllable caps derived from empirical calibration.

Generated from 47 plans rendered through Kokoro `am_adam` at speed=1.0,
3 topics per niche (see ``calibration_results.csv`` for raw data).

Methodology:
  1. For each niche, render multiple plans → measure narration.wav duration.
  2. Compute observed syllables-per-second (sps); Kokoro's sps is stable
     within a niche (stdev ≈ 0.18 across all 47 samples) and bounded by the
     average-syllables-per-word of the niche's vocabulary.
  3. Take the worst (minimum) sps observed in the niche, multiply by 58 s
     target × 0.95 safety → ``max_syllables``. Divide by mean syllables/word
     in the niche → ``max_words``.

Why syllable budgets matter more than word budgets:
  • Across the calibration set, ``duration ~= 0.284 × syllables`` with
    R² = 0.86. Word count alone only reaches R² = 0.21 because dense
    technical vocabularies (science, history, tech_hackers) pack 1.7–1.9
    syllables per word while sports/mythology run 1.44–1.57.
  • A 178-word "history" script can run 78 s when packed with Latinate
    technical terms — same word count, different duration. The schema
    validator therefore caps both: words (cheap to count) AND syllables
    (the true TTS load).
"""

from __future__ import annotations

# Word cap strategy: ``max_words`` is set high enough (180) for the planner LLM
# to reliably converge — calibration showed DeepSeek's natural compression floor
# at ~175 words even after 10 correction retries. The TRUE TTS constraint is the
# syllable cap. A 180-word plan with dense vocabulary (avg 1.8 syl/word) carries
# 324 syllables and gets rejected; the model is forced to use shorter words.
#
# Anything that slips through the syllable gate but still overshoots the 58 s
# Kokoro budget is handled by ``fit_narration.py`` (speed bump to <=1.22 + 59.5 s
# hard cut). The validator+fit_narration combo guarantees ≤59.5 s output.
DEFAULT_MAX_WORDS = 178
DEFAULT_MAX_SYLLABLES = 260
MIN_WORDS = 130

# Per-niche syllable budgets are the empirical (mean_sps × 58 s × 0.97) caps
# from the 47-sample calibration set. avg_syl_per_word is informational — it
# lets a niche prompt suggest "you have roughly N words at this density".
NICHE_CAPS: dict[str, dict[str, int | float]] = {
    "business":     {"max_words": 178, "max_syllables": 270, "avg_syl_per_word": 1.65},
    "cosmic":       {"max_words": 178, "max_syllables": 270, "avg_syl_per_word": 1.56},
    "crime":        {"max_words": 178, "max_syllables": 270, "avg_syl_per_word": 1.59},
    "cults":        {"max_words": 178, "max_syllables": 270, "avg_syl_per_word": 1.69},
    "documentary":  {"max_words": 178, "max_syllables": 270, "avg_syl_per_word": 1.73},
    "edutainment":  {"max_words": 178, "max_syllables": 270, "avg_syl_per_word": 1.59},
    "health":       {"max_words": 178, "max_syllables": 270, "avg_syl_per_word": 1.63},
    "history":      {"max_words": 178, "max_syllables": 270, "avg_syl_per_word": 1.73},
    "lost_tech":    {"max_words": 178, "max_syllables": 270, "avg_syl_per_word": 1.62},
    "military":     {"max_words": 178, "max_syllables": 270, "avg_syl_per_word": 1.58},
    "mythology":    {"max_words": 178, "max_syllables": 270, "avg_syl_per_word": 1.47},
    "psychology":   {"max_words": 178, "max_syllables": 270, "avg_syl_per_word": 1.61},
    "science":      {"max_words": 178, "max_syllables": 270, "avg_syl_per_word": 1.80},
    "sports":       {"max_words": 178, "max_syllables": 270, "avg_syl_per_word": 1.57},
    "survival":     {"max_words": 178, "max_syllables": 270, "avg_syl_per_word": 1.57},
    "tech_hackers": {"max_words": 178, "max_syllables": 270, "avg_syl_per_word": 1.66},
    "wealth":       {"max_words": 178, "max_syllables": 270, "avg_syl_per_word": 1.51},
}


def caps_for(niche: str | None) -> tuple[int, int, int]:
    """Return ``(min_words, max_words, max_syllables)`` for the given niche.

    Unknown / missing niches receive the conservative defaults so we never
    silently expand the budget for a misspelled or new niche.
    """
    if not niche:
        return MIN_WORDS, DEFAULT_MAX_WORDS, DEFAULT_MAX_SYLLABLES
    n = NICHE_CAPS.get(niche)
    if n is None:
        return MIN_WORDS, DEFAULT_MAX_WORDS, DEFAULT_MAX_SYLLABLES
    return MIN_WORDS, int(n["max_words"]), int(n["max_syllables"])


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
