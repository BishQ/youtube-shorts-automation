"""Five distinct prompt styles for the same NarrationPlan task.

Each style takes the SAME (niche, topic) input and asks for the SAME output
structure, but uses a different persuasion / framing strategy. We benchmark
which style each model performs best with, then merge the winning approach.

Style 1 — ULTRA-SIMPLE      — casual chat tone, minimal rules
Style 2 — FILM DIRECTOR     — cinematic-craft framing, fantasy-grade imagery
Style 3 — STRICT COUNTER    — explicit per-clause word counter, self-verify
Style 4 — FEW-SHOT EXAMPLE  — one full worked example, "do the same"
Style 5 — STEP-BY-STEP      — walks the model through each part sequentially

All styles return PLAIN-TEXT in the same labelled layout (HISTORICAL_FIGURE:,
CLAUSE 1:, etc.) so the existing multistage parser can read them all.
"""

from __future__ import annotations

from shorts_pipeline.planner.niche_caps import caps_for
from shorts_pipeline.planner.niches_compact import COMPACT_DATA


STYLE_NAMES = ["simple", "director", "counter", "fewshot", "stepwise"]


# Universal output spec block (kept identical across styles so we measure only
# the persuasion, not the schema).
def _output_spec_block() -> str:
    return """OUTPUT FORMAT — exactly this layout. No JSON, no markdown, no commentary:

HISTORICAL_FIGURE: <subject name>
COLD_OPEN_OBJECT: <one physical object, no person>
LEVER_TYPE: law | geography | politics
LEVER_DESC: <one sentence>
LEVER_CONSEQ: <one sentence>
LUT: <epic_warm | tragic_cold | ancient_sepia | dark_thriller | golden_hour>
END_QUESTION: <one rhetorical question ending in '?'>

CLAUSE 1: <text — first sentence is the curiosity-gap question under 14 words>
CLAUSE 2: <text>
...
CLAUSE 14: <text>

Then for every clause repeat:

CLAUSE 1:
  IMAGE: <40+ chars; MUST include one shot-type word (close-up | wide shot | medium shot |
    low-angle | high-angle | establishing | hero portrait | extreme close-up | tracking shot |
    top-down | over-the-shoulder | profile silhouette | aerial shot) AND one lighting word
    (candle | torch | dawn | dusk | glow | dim | sunlit | moonlit | backlit | torchlit |
    chiaroscuro | tungsten | golden hour | blue hour | flicker | haze | mist | soft light |
    natural light | twilight). Never use banned words: skyscraper, smartphone, swastika,
    then-and-now, morphing, comic panel, split screen.>
  MOTION: <20+ chars; one camera verb (push-in | pull-back | dolly | pan | tilt | orbit |
    tracks | zoom | static shot | handheld) + subject motion + atmosphere. Unique per clause.>
  FIGURE_PRESENT: true | false   (aim for ≥8 of 14 = true)
  EMOTION: hook | tense_buildup | suspense | reveal | triumphant | tragic | climactic | reflective | shock
  INTENSITY: 0.0-1.0
  CAMERA: ken_burns | pan | zoom_out | hold | parallax
  TRANSITION: hard_cut | xfade | dip_to_black | smash_white
  DURATION: short | medium | long
  COLOR: epic_warm | tragic_cold | ancient_sepia | dark_thriller | golden_hour | null
  AUDIO: none | low_rumble | impact | paper_flutter | crowd_cheer | sword_clash | horse_gallop | fire_crackle | thunder_crack | crowd_murmur
  EMPHASIS: word1 | word2 | word3
  TIER: grounded | cinematic | legendary
  SUBTITLE: top | middle | bottom
  CUT_TARGET: <object or null>
"""


# ── STYLE 1 — CLEAN-BRIEF ─────────────────────────────────────────────────────
# Plain, no fluff, but ALL essential rules present.

def system_simple(niche: str) -> str:
    min_w, max_w, max_syl = caps_for(niche)
    d = COMPACT_DATA[niche]
    banned = "; ".join(d["banned"][:6])
    return f"""You write {d['voice']} for YouTube Shorts.

RULES (all required):
- {min_w}-{max_w} words TOTAL across exactly 14 clauses. ≤{max_syl} syllables.
- Clause 1's first sentence is a curiosity-gap question, under 14 words.
- END_QUESTION is the ONLY OTHER question mark in the whole output.
- Niche scope: {d['scope']}.
- Transformation (decision_lever.description): {d['transformation']}.
- Banned phrases: {banned}; changed history; shaped the world.
- Visual voice: {d['visual']}. Color grade: {d['color_grade']}.
- Every IMAGE includes a SHOT TYPE word AND a LIGHTING word.
- Every MOTION starts with a camera verb. No two MOTION values identical.
- Banned image content: skyscraper, smartphone, laptop, cell phone, mobile phone, neon sign, swastika, then-and-now, morphing, comic panel, split screen.
- Never use the subject's name inside IMAGE — describe by role + period dress + ONE feature.

{_output_spec_block()}"""


def user_simple(topic: str, niche: str) -> str:
    min_w, max_w, _ = caps_for(niche)
    return f"Topic: {topic!r}.\nTarget: {min_w}-{max_w} words, 14 clauses. Professional quality."


# ── STYLE 2 — FILM DIRECTOR ───────────────────────────────────────────────────

def system_director(niche: str) -> str:
    min_w, max_w, max_syl = caps_for(niche)
    d = COMPACT_DATA[niche]
    banned = "; ".join(d["banned"][:6])
    return f"""You are an AWARD-WINNING FILM DIRECTOR writing the opening 58 seconds of a cinematic Short.

Voice: {d['voice']}. Think Christopher Nolan + David Attenborough.

Every IMAGE prompt is a real frame — readable, period-accurate, fantasy-grade detail. {d['visual']}.
Every MOTION prompt is a single camera move plus subject action plus atmosphere — what the lens does.

NARRATION:
  - {min_w}-{max_w} words total, ≤{max_syl} syllables. The TTS reads at ~5.2 syl/sec.
  - 14 clauses, the first opens with a question under 14 words.
  - Exactly two question marks: clause 1 first sentence and END_QUESTION.
  - Banned: {banned}; "changed history"; "shaped the world".

VISUALS:
  - Treat each frame like a film still: shot type + lighting + composition + props.
  - Colour grade: {d['color_grade']}.
  - Banned image content: skyscraper, smartphone, laptop, cell phone, mobile phone, neon sign, swastika, then-and-now, morphing, comic panel, split screen.
  - Never put the subject's name inside IMAGE — describe by role + period dress + ONE distinctive feature.

{_output_spec_block()}"""


def user_director(topic: str, niche: str) -> str:
    return f"Direct the opening short about: {topic!r}.\nMake every frame a film still. Make every clause earn its place. Hit the word budget exactly."


# ── STYLE 3 — STRICT COUNTER ──────────────────────────────────────────────────

def system_counter(niche: str) -> str:
    min_w, max_w, max_syl = caps_for(niche)
    per_clause = max_w // 14
    d = COMPACT_DATA[niche]
    banned = "; ".join(d["banned"][:6])
    return f"""You write {d['voice']}.

WORD BUDGET — THIS IS THE PRIMARY RULE.
  Total full script: {min_w}-{max_w} words. NOT less. NOT more.
  Target per clause: {per_clause-2}-{per_clause+1} words. Some shorter, some longer is fine, but the SUM stays in range.
  Syllables: ≤{max_syl} total (Kokoro TTS reads ~5.2 syl/sec; this is a 57-sec body).

SELF-VERIFY before writing the OUTPUT:
  1. Draft each of the 14 clauses with target length.
  2. Add the word counts. If sum < {min_w}, expand the weakest 2 clauses.
  3. If sum > {max_w}, trim the heaviest 2 clauses.
  4. If a clause has heavy Latinate words ("characteristics", "demonstration",
     "investigation", "revolutionary"), swap for shorter equivalents.
  5. Only THEN write the output below.

NICHE SCOPE: {d['scope']}.
TRANSFORMATION: {d['transformation']}.

OPENING: Clause 1's first sentence is a question under 14 words. End_question is the only other question mark.

BANNED PHRASES in narration: {banned}; changed history; shaped the world.

VISUAL VOICE: {d['visual']}. Color grade: {d['color_grade']}.
Every IMAGE has a SHOT TYPE word AND a LIGHTING word.
Every MOTION starts with a camera verb — each unique.
Banned image content: skyscraper, smartphone, laptop, cell phone, mobile phone, neon sign, swastika, then-and-now, morphing, comic panel, split screen.
Never use the subject's name inside IMAGE — role + period dress + ONE feature only.

{_output_spec_block()}"""


def user_counter(topic: str, niche: str) -> str:
    min_w, max_w, _ = caps_for(niche)
    return f"Topic: {topic!r}.\nDraft → verify word count (target {min_w}-{max_w}) → produce final output. Show only the final output, not the draft."


# ── STYLE 4 — FEW-SHOT EXAMPLE ────────────────────────────────────────────────

# A minimal but high-quality example of the format. Kept compact so prompt
# token cost stays low.
_FEWSHOT_EXAMPLE = """EXAMPLE OUTPUT (history niche, topic 'The defenestration of Prague, 1618'):

HISTORICAL_FIGURE: The three regents at Prague Castle
COLD_OPEN_OBJECT: a brass window-latch with a strip of cloth caught in its hinge
LEVER_TYPE: politics
LEVER_DESC: On 23 May 1618, Bohemian Protestant nobles threw two imperial regents and their secretary from a third-floor castle window.
LEVER_CONSEQ: The act detonated the Thirty Years' War; eight million people died before it ended in 1648.
LUT: dark_thriller
END_QUESTION: If a single open window in your city would cost a continent thirty years, would you have closed it?

CLAUSE 1: Why did three men survive a fall from a castle window?
CLAUSE 2: It was the morning of 23 May 1618.
CLAUSE 3: The nobles had brought a written accusation against the regents.
CLAUSE 4: The regents had brought no defence.
CLAUSE 5: The chamber was cold and the windows were closed.
CLAUSE 6: Then the windows were opened.
CLAUSE 7: Two regents and their secretary went out, sixty-nine feet to the ground.
CLAUSE 8: All three survived the impact.
CLAUSE 9: Catholic chroniclers said angels intervened.
CLAUSE 10: Protestants said the dung-heap below caught them.
CLAUSE 11: Everyone knows the war that followed lasted thirty years.
CLAUSE 12: That morning, nobody in the chamber expected a war.
CLAUSE 13: They expected a precedent — and it travelled across the continent within a week.
CLAUSE 14: Eight million people would perish before the treaty in 1648.

CLAUSE 1:
  IMAGE: low-angle wide shot of a high-vaulted council chamber, three imperial regents rising from a heavy oak bench, twenty Bohemian noblemen in black-and-cream doublets mid-stride, leaded-glass window mid-frame, tallow-amber candle light through clerestory haze, 17th-century documentary realism
  MOTION: camera slow push-in, the seated regents rise one inch from the bench, candle smoke drifts past the leaded-glass pane
  FIGURE_PRESENT: true
  EMOTION: hook
  INTENSITY: 0.85
  CAMERA: ken_burns
  TRANSITION: hard_cut
  DURATION: medium
  COLOR: dark_thriller
  AUDIO: low_rumble
  EMPHASIS: window | fall
  TIER: cinematic
  SUBTITLE: middle
  CUT_TARGET: window

[... 13 more visual blocks in the same shape ...]

END OF EXAMPLE."""


def system_fewshot(niche: str) -> str:
    min_w, max_w, max_syl = caps_for(niche)
    d = COMPACT_DATA[niche]
    return f"""You write {d['voice']}.

{_FEWSHOT_EXAMPLE}

Now produce the same shape for the requested topic.

CONSTRAINTS:
  - Word budget: {min_w}-{max_w} words across 14 clauses, ≤{max_syl} syllables.
  - Niche scope: {d['scope']}.
  - Visual voice: {d['visual']}.
  - Two question marks total: clause 1 first sentence, and END_QUESTION.
  - Every IMAGE needs a shot type + lighting word.
  - No banned image words (skyscraper, smartphone, laptop, cell phone, mobile phone, neon sign, swastika, then-and-now, morphing, comic panel, split screen).
  - No banned phrases: {'; '.join(d['banned'][:5])}; changed history; shaped the world.

{_output_spec_block()}"""


def user_fewshot(topic: str, niche: str) -> str:
    return f"Same shape as the example, but the topic is: {topic!r}.\nMatch the cinematic-still quality of every IMAGE field."


# ── STYLE 5 — STEP-BY-STEP ────────────────────────────────────────────────────

def system_stepwise(niche: str) -> str:
    min_w, max_w, max_syl = caps_for(niche)
    d = COMPACT_DATA[niche]
    banned = "; ".join(d["banned"][:6])
    return f"""You write {d['voice']}.

You will work through five internal steps before producing the output. Do them silently — the user sees ONLY the final output.

STEP 1 — Pick the curiosity-gap question for clause 1 (under 14 words, ends with '?').
         END_QUESTION will be the only other '?' in the entire output.
STEP 2 — List five documented facts about the topic you'll weave into the narration.
         Niche scope: {d['scope']}.
         Transformation: {d['transformation']}.
STEP 3 — Draft clauses 2–14. Each clause: 12–15 words. Weave the five facts naturally.
         Target total: {min_w}-{max_w} words. Verify syllables ≤{max_syl}. Use plain Anglo-Saxon English.
         Banned phrases: {banned}; changed history; shaped the world.
STEP 4 — Design one IMAGE per clause: shot type + lighting + period-accurate visual detail
         in {d['visual']} register. Color grade preference: {d['color_grade']}.
         Banned content: skyscraper, smartphone, laptop, cell phone, mobile phone, neon sign, swastika, then-and-now, morphing, comic panel,
         split screen. Never include the subject's name inside IMAGE.
STEP 5 — Pair each clause with a MOTION (camera verb + subject motion + atmosphere, unique
         per clause), and a BEAT (emotion + intensity + camera enum + transition + duration
         + color + audio + emphasis words + tier + subtitle position + cut target).

Only after all five steps, write the output below.

{_output_spec_block()}"""


def user_stepwise(topic: str, niche: str) -> str:
    return f"Walk through the five steps internally for topic: {topic!r}, then produce only the final output."


# ── Public dispatch ──────────────────────────────────────────────────────────

STYLES = {
    "simple":   (system_simple,   user_simple),
    "director": (system_director, user_director),
    "counter":  (system_counter,  user_counter),
    "fewshot":  (system_fewshot,  user_fewshot),
    "stepwise": (system_stepwise, user_stepwise),
}


def build_prompts(style: str, niche: str, topic: str) -> tuple[str, str]:
    if style not in STYLES:
        raise ValueError(f"Unknown style {style!r}. Available: {list(STYLES)}")
    sys_fn, user_fn = STYLES[style]
    return sys_fn(niche), user_fn(topic, niche)


__all__ = ["STYLES", "STYLE_NAMES", "build_prompts"]
