"""Build ASS subtitles — word-by-word karaoke highlight style.

Design (BorderStyle 1 — universally supported by all libass/FFmpeg versions):
  • Active word    — bright yellow text + black outline + gentle scale 118 → 100 %.
  • Inactive words — plain white text at ~65 % opacity, same outline.
  • Emphasis words — vivid orange/red text + extra scale hold + blur glow.
  • No background boxes — clean text-only karaoke, works on any background.
  • Chunk groups    — ≤12 chars (settable) per visible group, split at whitespace.
  • Min duration    — last event of each group holds for at least 1.2 s (capped
                      30 ms before the next group to prevent overlap).
  • narration_end_s — last event extended to audio duration so text stays visible
                      through the TTS tail.
"""

from __future__ import annotations

import math
import re as _re
import unicodedata
from pathlib import Path

from shorts_pipeline.aligner.base import WordSpan
from shorts_pipeline.aligner.clause_times import clause_word_offsets, ensure_word_spans_for_plan
from shorts_pipeline.config.settings import Settings
from shorts_pipeline.planner.schema import EmotionType, NarrationPlan, SubtitlePosition


def _ass_time(s: float) -> str:
    if s < 0:
        s = 0.0
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = s - 60 * m - 3600 * h
    cs = int(round((sec - math.floor(sec)) * 100))
    return f"{h:d}:{m:02d}:{int(math.floor(sec)):02d}.{cs:02d}"


def _escape_ass(s: str) -> str:
    return s.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def _norm_words(text: str) -> list[str]:
    return [w for w in _re.findall(r"[\w']+|[^\w\s]", text) if w.strip()]


def _has_alnum(word: str) -> bool:
    """True when the word contains at least one letter or digit.

    Uses this instead of a regex so that edge-cases like apostrophes,
    curly quotes, and Unicode punctuation all resolve correctly — any
    word that is purely punctuation/symbols is excluded.
    """
    return any(c.isalnum() for c in word)


def _strip_leading_commas_subtitle_line(text: str) -> str:
    """ASS Dialogue Text field: remove leading ASCII/fullwidth commas only.

    Repeats after leading override blocks ``{...}`` and whitespace so
    ``{\\fad(1,2)},,WORD`` keeps the comma inside ``fad`` but drops the two
    before ``WORD``. Does not remove commas after the first visible character.
    """
    s = text
    cap = max(32, len(s) + 4)
    for _ in range(cap):
        t = s
        n = len(t)
        i = 0
        while i < n and t[i] in " \t":
            i += 1
        while i < n and t[i] == "{":
            depth = 0
            j = i
            while j < n:
                if t[j] == "{":
                    depth += 1
                elif t[j] == "}":
                    depth -= 1
                    if depth == 0:
                        j += 1
                        break
                j += 1
            else:
                break
            i = j
        while i < n and t[i] in " \t":
            i += 1
        # Leading junk after overrides: commas / fullwidth comma / open parens
        # (Whisper sometimes yields "(,word"; tags must not hide the "(" from here.)
        _LEAD_VISUAL_JUNK = ",，(（"
        if i < n and t[i] in _LEAD_VISUAL_JUNK:
            j = i
            while j < n and t[j] in _LEAD_VISUAL_JUNK:
                j += 1
            while j < n and t[j] in " \t":
                j += 1
            s = t[:i] + t[j:]
            continue
        break
    return s.lstrip()


def _strip_edge_non_alnum(s: str) -> str:
    """Remove leading/trailing junk that is not a letter or digit (Unicode-aware).

    Catches tokens like ``(,word`` or ``…`` after outer P* stripping, so on-screen
    text is word-focused without stray brackets or commas at the edges.
    """
    s = s.strip()
    while s and not s[0].isalnum():
        s = s[1:].lstrip()
    while s:
        ch = s[-1]
        if ch.isalnum():
            break
        if ch in "'\u2019" and len(s) >= 2 and s[-2].isalpha():
            break
        s = s[:-1].rstrip()
    return s


def _strip_outer_punctuation(s: str) -> str:
    """Drop leading/trailing punctuation (Unicode class P*).

    Whisper and reference alignment often attach commas or quotes to the next
    token (e.g. ",ONLY" or a full-width comma). ASCII-only lstrip/rstrip misses
    those, so subtitles could start with a visible comma.

    Trailing ASCII/typographic apostrophe is kept when it closes a contraction
    (letter before it), so ``it's`` / ``boys'`` are not damaged.
    """
    s = s.strip()
    while s:
        ch = s[0]
        if unicodedata.category(ch)[0] != "P":
            break
        s = s[1:].strip()
    while s:
        ch = s[-1]
        if unicodedata.category(ch)[0] != "P":
            break
        if ch in "'\u2019" and len(s) >= 2 and s[-2].isalpha():
            break
        s = s[:-1].strip()
    return s


def _subtitle_display(raw: str, *, use_upper: bool) -> str:
    """Clean token for on-screen subtitle text."""
    r = raw.strip()
    cleaned = _strip_outer_punctuation(r)
    cleaned = _strip_edge_non_alnum(cleaned)
    if not cleaned:
        cleaned = _strip_edge_non_alnum(r) or ""
    # Do not fall back to raw: punctuation-only tokens (e.g. a stray comma span
    # from alignment) would otherwise render as their own dim karaoke “word”.
    if not any(ch.isalnum() for ch in cleaned):
        cleaned = ""
    return cleaned.upper() if use_upper else cleaned


def _split_chunks(
    words: list[WordSpan],
    max_chars: int,
    max_words: int,
    use_upper: bool,
) -> list[list[WordSpan]]:
    """Split a word list into visible groups.

    A new group is started when EITHER limit would be exceeded:
      • total characters (words joined with one space) > max_chars
      • word count == max_words

    A single word that already exceeds max_chars is kept as its own group.
    """
    if max_words <= 0 and max_chars <= 0:
        return [words]

    groups: list[list[WordSpan]] = []
    current: list[WordSpan] = []
    current_chars = 0

    for w in words:
        raw = w.word.strip()
        display = _subtitle_display(raw, use_upper=use_upper)
        wlen = len(display)
        added = wlen if not current else wlen + 1  # +1 for space separator

        word_limit_hit = max_words > 0 and len(current) >= max_words
        char_limit_hit = max_chars > 0 and current and current_chars + added > max_chars

        if word_limit_hit or char_limit_hit:
            groups.append(current)
            current = [w]
            current_chars = wlen
        else:
            current.append(w)
            current_chars += added

    if current:
        groups.append(current)

    return groups


# ── Position tags ──────────────────────────────────────────────────────────────
_POS_TAG: dict[SubtitlePosition, str] = {
    SubtitlePosition.bottom: "",
    SubtitlePosition.middle: r"{\an5}",
    SubtitlePosition.top:    r"{\an8}",
}

# ── Emotion → UPPERCASE flag + optional extra chunk scale ─────────────────────
_EMOTION_STYLE: dict[EmotionType, tuple[bool, str]] = {
    EmotionType.hook:          (True,  r"{\fscx108\fscy108}"),
    EmotionType.shock:         (True,  r"{\fscx112\fscy112}"),
    EmotionType.climactic:     (True,  r"{\fscx110\fscy110}"),
    EmotionType.tense_buildup: (False, ""),
    EmotionType.suspense:      (False, ""),
    EmotionType.reveal:        (False, r"{\fscx105\fscy105}"),
    EmotionType.triumphant:    (True,  r"{\fscx107\fscy107}"),
    EmotionType.tragic:        (False, ""),
    EmotionType.reflective:    (False, ""),
}

# ── Per-emotion active-word highlight colour (ASS &H00BBGGRR) ─────────────────
_EMOTION_ACTIVE_COLOUR: dict[EmotionType, str | None] = {
    EmotionType.hook:          None,             # default yellow
    EmotionType.shock:         "&H003040FF",     # vivid red-orange
    EmotionType.climactic:     "&H000070FF",     # orange
    EmotionType.triumphant:    "&H0000C0FF",     # warm gold
    EmotionType.reveal:        "&H00F0F0FF",     # near-white
    EmotionType.tense_buildup: None,
    EmotionType.suspense:      "&H00C06000",     # deep amber
    EmotionType.tragic:        "&H00E0C0C0",     # muted rose
    EmotionType.reflective:    "&H00F5E0C0",     # warm cream
}


def build_ass_karaoke(
    plan: NarrationPlan,
    words: list[WordSpan],
    settings: Settings,
    out_path: Path,
    *,
    narration_end_s: float | None = None,
) -> Path:
    """
    Word-by-word karaoke (BorderStyle 1).

    Active word  : highlight colour text + black outline + gentle scale-in.
    Inactive     : white text at ~55 % opacity + same outline.
    No background boxes — works on every libass / FFmpeg build.

    Args:
        narration_end_s: When provided the last subtitle event is extended
            to this timestamp so subtitles don't vanish before audio ends.
    """
    bold_flag = "-1" if settings.ass_bold else "0"

    header = (
        f"[Script Info]\n"
        f"Title: shorts\n"
        f"ScriptType: v4.00+\n"
        f"WrapStyle: 0\n"
        f"ScaledBorderAndShadow: yes\n"
        f"PlayResX: {settings.video_width}\n"
        f"PlayResY: {settings.video_height}\n"
        f"\n"
        f"[V4+ Styles]\n"
        f"Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        f"OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        f"ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        f"Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,"
        f"{settings.ass_font_name},{settings.ass_font_size},"
        f"{settings.ass_primary_colour},"       # white text
        f"{settings.ass_secondary_colour},"
        f"{settings.ass_outline_colour},"       # black outline
        f"{settings.ass_back_colour},"
        f"{bold_flag},0,0,0,"
        f"100,100,0,0,"
        f"1,{settings.ass_outline},{settings.ass_shadow},"   # BorderStyle=1
        f"2,40,40,{settings.ass_margin_v},1\n"
        f"\n"
        f"[Events]\n"
        f"Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    words = ensure_word_spans_for_plan(plan, words)
    offsets = clause_word_offsets(plan)
    events: list[str] = []

    chunk_sz    = settings.subtitle_chunk_size
    max_chars   = getattr(settings, "subtitle_max_chunk_chars", 0)
    min_group_s = getattr(settings, "subtitle_min_group_duration_s", 0.0)
    hl_default  = settings.subtitle_highlight_color     # yellow &H0000FFFF
    inactive_a  = settings.subtitle_inactive_alpha      # dim alpha for inactive text
    emph_colour = getattr(settings, "subtitle_emphasis_glow", "&H001040FF")
    base_outline = max(1, int(settings.ass_outline))
    active_outline = base_outline + 1
    inactive_shadow = max(1, int(settings.ass_shadow) - 1)
    active_shadow = max(1, int(settings.ass_shadow) - 2)

    for clause, (start_idx, end_idx) in zip(plan.clauses, offsets):
        cw = words[start_idx:end_idx]
        if not cw:
            continue

        use_upper, emotion_tag = _EMOTION_STYLE.get(clause.beat.emotion, (False, ""))

        # Filter standalone punctuation tokens — any word with NO alphanumeric
        # characters (commas, periods, quotes, etc.) is silently skipped.
        cw = [w for w in cw if _has_alnum(w.word)]
        cw = [
            w
            for w in cw
            if _subtitle_display(w.word.strip(), use_upper=use_upper).strip()
        ]
        if not cw:
            continue

        emphasis_set = {e.lower().strip(".,!?;:\"'") for e in clause.beat.emphasis_words}
        pos_tag = _POS_TAG.get(clause.beat.subtitle_position, "")

        hl_active = _EMOTION_ACTIVE_COLOUR.get(clause.beat.emotion) or hl_default

        all_groups = _split_chunks(cw, max_chars=max_chars, max_words=chunk_sz, use_upper=use_upper)

        for gi, group in enumerate(all_groups):
            if not group:
                continue
            n = len(group)
            group_start_s = group[0].start_s

            next_group_start = (
                all_groups[gi + 1][0].start_s
                if gi + 1 < len(all_groups) and all_groups[gi + 1]
                else float("inf")
            )

            for active_i, active_word in enumerate(group):
                t_start = active_word.start_s
                t_end   = active_word.end_s
                if t_end <= t_start:
                    t_end = t_start + 0.10

                # Enforce minimum group visibility on the last word.
                # Cap at 30 ms before the next group to prevent overlap.
                if active_i == n - 1 and min_group_s > 0:
                    desired = group_start_s + min_group_s
                    cap     = next_group_start - 0.03
                    t_end   = max(t_end, min(desired, cap))

                fade_in  = 55 if active_i == 0     else 0
                fade_out = 35 if active_i == n - 1 else 0

                parts: list[str] = []
                for i, w in enumerate(group):
                    raw = w.word.strip()
                    display = _subtitle_display(raw, use_upper=use_upper)
                    tok = _escape_ass(display)
                    bare = _strip_outer_punctuation(raw).lower()
                    spacer = " " if i < n - 1 else ""

                    if i == active_i:
                        if bare in emphasis_set:
                            # Emphasis: vivid colour + mild blur, smooth settle (no snap)
                            tag = (
                                f"{{\\1c{emph_colour}&\\1a&H00&"
                                f"\\bord{active_outline + 1}\\shad{active_shadow}"
                                f"\\fscx122\\fscy122"
                                f"\\t(0,180,\\fscx106\\fscy106)"
                                f"\\t(180,320,\\fscx100\\fscy100)"
                                f"\\blur1.0}}"
                            )
                        else:
                            # Normal active: highlight + single smooth scale-in
                            tag = (
                                f"{{\\1c{hl_active}&\\1a&H00&"
                                f"\\bord{active_outline}\\shad{active_shadow}"
                                f"\\fscx112\\fscy112"
                                f"\\t(0,210,\\fscx100\\fscy100)"
                                f"\\blur0.35"
                                f"}}"
                            )
                        parts.append(f"{tag}{tok}{{\\r}}{spacer}")
                    else:
                        # Inactive: plain white at reduced opacity
                        parts.append(
                            f"{{\\1a{inactive_a}&\\bord{base_outline}\\shad{inactive_shadow}"
                            f"\\blur0.15\\fscx94\\fscy94}}{tok}{{\\r}}{spacer}"
                        )

                fade_tag = f"{{\\fad({fade_in},{fade_out})}}"
                text = fade_tag + pos_tag + emotion_tag + "".join(parts)
                text = _strip_leading_commas_subtitle_line(text)

                events.append(
                    f"Dialogue: 0,{_ass_time(t_start)},{_ass_time(t_end)},"
                    f"Default,,0,0,0,,{text}"
                )

    # Extend the very last event to the actual narration duration so subtitles
    # don't vanish before the audio tail finishes.
    if events and narration_end_s is not None and narration_end_s > 0:
        last = events[-1]
        parts_split = last.split(",", 9)
        if len(parts_split) >= 3:
            parts_split[2] = _ass_time(float(narration_end_s))
            events[-1] = ",".join(parts_split)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    return out_path
