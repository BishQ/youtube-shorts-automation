"""Universal craft rules shared by every niche template.

Each niche SYSTEM_PROMPT embeds SHARED_CRAFT after its niche-specific
subject rules. The rules below are niche-agnostic — they govern rhythm,
images, banned phrases, and the mobile silhouette test that every 10/10
Short obeys regardless of topic.
"""

SHARED_CRAFT = """\
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
UNIVERSAL CRAFT LAWS (apply to every niche, every clause)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

RULE U1 — ONE EMOTIONAL CORE
• Before writing one word, name the TRANSFORMATION at the heart of this story in one phrase.
• Transformations beat events. "Believer → traitor" beats "He left the company."
• Every clause must serve that phrase. If a clause does not advance the transformation, cut it.

RULE U2 — COMPRESSION (Shorts law)
• Imply, don't explain. Every clause is the tip of an iceberg.
• ✗ BAD:  "His decisions would change the entire industry forever."
• ✓ GOOD: "The industry would never write a contract the same way again."
• Cut every word that does not earn its place. Three words is often the strongest sentence.

RULE U3 — EMOTIONAL ARC (14 clauses, fixed)
    Clauses 1–2:   HOOK — clause 1 is the curiosity-gap question + iconic prime image.
                   Clause 2 flashes back / drops into the origin moment.
    Clauses 3–5:   RISING ACTION — specifics with stakes, not exposition.
    Clauses 6–8:   CRISIS — the contradiction lives here. Two sides. The cost.
    Clauses 9–11:  CLIMAX — one irreversible moment. Short sentences. High impact.
    Clauses 12–14: RESONANCE — zoom out. One haunting truth. End on IMAGE/ACTION, not biography.

RULE U4 — TWO QUESTIONS, TWO JOBS (and ONLY two)
• Q1: the first sentence of clause 1 (curiosity-gap, ≤14 words, never yes/no).
• Q2: the end_plate_question (personal moral challenge — "would you", "could you").
• Zero other "?" anywhere. No rhetorical mid-script questions.

RULE U5 — RHYTHM + PATTERN INTERRUPTS
• Alternate sentence length aggressively. Short punch → flow → short punch.
• Use the 3-word sentence at peaks. "He didn't blink."
• Minimum 2 pattern interrupts: a single-word clause, a tonal hard cut, an undercutting reframe.

RULE U6 — THE REFRAME (clauses 6–10)
• Take something the viewer thinks they know and flip it.
• Structure: "Everyone knows [X]. What nobody talks about is [Y]."
• This is the screenshot moment. It is what makes them feel smarter for watching.

RULE U7 — CONTRADICTION REQUIRED
• At least one paradox or irony, placed in clauses 7–10.
• "The man who built the lock company died because he lost the key to his own safe."

RULE U8 — SENSORY DETAIL (≥2 clauses use non-visual)
• Sound, weight, smell, temperature, texture. Embodied memory → retention.

RULE U9 — ACTIVE VOICE ONLY
• Passive voice kills momentum. "He ended it," not "It was ended by him."

RULE U10 — MACRO REFRAME ENDING (clause 14)
• Reframes the whole story in one sentence. The line the viewer carries away.
• Never a question. Never biography. Always a concrete image or reframe.

RULE U11 — TTS-FRIENDLY RHYTHM (Kokoro reads at ~4.5 syllables/sec at 1.0× speed; the
script must fit a 58 s body window inside the 60 s Shorts cap)
• Empirical truth from 47-sample calibration: Kokoro's pacing is governed by SYLLABLE count,
  not word count. Two scripts with identical 175-word counts varied from 54 s to 83 s purely
  because one had 260 syllables and the other had 350.
• Your niche carries a per-niche syllable budget (~220–255 syllables). The schema validator
  rejects anything over budget. Stay clear of dense polysyllabic vocabulary.
• ✗ BANNED in narration text: foreign-language book/work titles in their original language
  ("Mirifici Logarithmorum Canonis Descriptio", "De Revolutionibus Orbium Coelestium",
  "Disquisitiones Arithmeticae"). Refer to them in plain English: "his book of logarithm tables",
  "his treatise on the heavens", "his number-theory masterwork".
• Foreign-language single words used in English (zeitgeist, samurai, glasnost) are fine.
  Whole foreign phrases or titles are not.
• Replace 4+ syllable Latinate words with shorter equivalents whenever the meaning survives:
  ✗ "extraordinarily"   → ✓ "incredibly"
  ✗ "demonstration"     → ✓ "proof"
  ✗ "characteristics"   → ✓ "traits"
  ✗ "logarithmic tables"→ ✓ "log tables"
  ✗ "investigation"     → ✓ "probe"
• Sentence terminators ('.', '!', '?') do NOT meaningfully expand TTS duration — the old
  "fewer periods = faster" hypothesis was falsified by the calibration data (R² = 0.29
  vs syllables R² = 0.86). Keep U5's pattern interrupts; the syllable budget alone is the
  hard TTS gate now.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
UNIVERSAL BANNED PHRASES (any appearance = automatic 4/10, rewrite)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✗ "changed everything" / "changed the world" / "changed history"
✗ "shook the world" / "stunned the world" / "shocked everyone"
✗ "you won't believe" / "the truth will shock you"
✗ "rose to power" / "rose from nothing"
✗ "the rest is history"
✗ "for better or worse"
✗ "still echoes today" / "would echo through the centuries"
✗ "a name that would live on"
✗ "the legal document/decision/moment that changed everything"
✗ Any sentence where swapping the subject for a different one in the same niche still works.
  If the line is portable, it is too generic. Replace with something only THIS story can say.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
IMAGE LAWS — APPLY TO EVERY image_prompt
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

LAW I1 — CLAUSE 1 IS THE PRIME IMAGE
• Hero/subject at their MOST ICONIC moment in their MOST FAMOUS arena.
• Scale (crowd / environment / city). Colour identity. Atmospheric element. Strong silhouette.
• The cold_open_object appears in clause 1 as a BARELY-VISIBLE secondary element.

LAW I2 — HUMAN PRESENCE IS DEFAULT
• ≥8 of 10 clauses must show a human acting. Empty rooms allowed at most TWICE total.

LAW I3 — PEAK MOMENT, NOT POSED
• Use a mid-* action verb (mid-stride, mid-turn, mid-flinch, mid-realise, mid-grip).
• High intensity → physical mid-action. Low intensity → "charged stillness" (mid-pause, mid-glance).

LAW I4 — COLOUR ANCHORS (mandatory in every image_prompt)
• Name the dominant colour, the contrast colour, the named light source.
• "drenched in Wall Street navy, paper-white contrast, blazing trading-floor LEDs"
• Banned: "muted earth tones", "pale" without contrast, abstract "cinematic lighting".

LAW I5 — MOBILE SILHOUETTE TEST
• Blurred to 20% sharpness on a 5-inch screen — would the core subject still read?
• ONE dominant shape, strong edge contrast, clear subject/background separation.
• ≥3 silhouette-first compositions across the 14 clauses.

LAW I6 — STYLE TIERS (style intensity must match emotional intensity)
  TIER A — GROUNDED (60–70%): "observational documentary realism, available light".
  TIER B — CINEMATIC (≈20%): one elevation element only — harsh light OR strong shadow OR
           shallow DoF. Never stack.
  TIER C — LEGENDARY (≤2–3 clauses total, never two in a row, only at hook + climax):
           "epic blockbuster cinema, god-rays piercing smoke, IMAX frame".
• Tier C on every clause = "AI slideshow" tell. Contrast is impact.

LAW I7 — VISUAL VARIETY
• No color_grade value appears more than 3× across the 14 clauses.
• No camera motion repeats twice in a row.
• Alternate intimate (close-up) and epic (wide) every 2–3 clauses.
• Vary mid-* action verbs — never use the same one twice.

LAW I8 — STYLE FAMILY DISCIPLINE
• Pick ONE coherent visual genre per prompt and commit. Mixing anime + photoreal + painterly
  + noir averages to mud. Each prompt: subject → environment → named light → lens → ONE style read.

REQUIRED IMAGE_PROMPT FORMAT (all 7 parts, in this order):
  [SHOT TYPE]. [SUBJECT + PHYSICAL DETAIL]. [ACTION MOMENT — what is happening THIS instant].
  [ERA / ENVIRONMENT-ACCURATE SETTING]. [NAMED LIGHTING]. [SINGLE STYLE TAG]. [RESOLUTION TAIL].

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
LAW I9 — UNIVERSAL HIGH-RESOLUTION IMAGE FLOOR (every image_prompt, every niche)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Every image_prompt MUST end with this resolution + detail tail, AFTER the single style tag,
  as the LAST clause of the prompt — copy this string verbatim:
  "ultra-detailed, photoreal micro-texture, tack-sharp focal subject, crisp edge contrast,
   8K render quality, no AI-blur, no plastic skin, no waxy highlights, no soft background haze."
• Detail floor — must be visible at thumbnail scale:
  – Faces (where permitted): skin pore detail, eyelash separation, hair-strand definition.
  – Materials: weave, grain, patina, scratch, wear — named, never "a generic surface".
  – Objects: tool-mark, edge crispness, label legibility, fingerprint-grade focal sharpness.
• Forbidden as STYLE (still allowed as DIEGETIC effect — CCTV, VHS-archive, 16mm period film,
  microscope blur, museum-glass reflection — but clearly labelled as such, never the default):
  "blurry", "lo-fi", "soft focus", "dreamy haze", "low resolution".
• Niche templates may layer ADDITIONAL niche-specific detail-floor guidance on top — those
  additions REINFORCE this universal rule, they never override it.
• High resolution does NOT mean cluttered — LAW I5 still binds: one dominant subject, strong
  edge separation, clean read at 5-inch screen + 20% blur.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
QUANTITATIVE PRE-OUTPUT GATES (validator rejects on)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  • Clauses: EXACTLY 14.
  • full_script word count: see your niche's word budget (passed in the user prompt).
    Budgets are calibrated per-niche from real Kokoro TTS runs — they are NOT 178 across
    the board. Science/history budgets are ~135–140 words; sports/mythology run 160+.
  • full_script syllable count: stays under the niche syllable budget. Long Latinate words
    (logarithmic, demonstration, characteristics) burn the budget twice as fast as plain
    English. Prefer short concrete words: "logs", "proof", "traits".
  • Question marks: EXACTLY 2 (clause 1 hook + end_plate_question).
  • Clause 1 text starts with a curiosity-gap question ≤14 words.
  • Clause 1 image: hero/subject portrait, face dominant, cold_open_object secondary.
  • ≥8 of 14 clauses show a human. ≥3 silhouette-first compositions.
  • No color_grade > 3× total. No camera repeats twice in a row.
  • ≥2 short-punch clauses (≤5 words). ≥2 pattern interrupts.
  • ≥1 reframe in clauses 6–10. ≥2 clauses with non-visual sensory detail.
  • Final clause: concrete image / macro reframe — never question, never biography.
  • Zero banned phrases. Zero fabricated facts.
  • Zero graphic blood/gore — imply violence through aftermath.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
COPYRIGHT / LIKENESS / TRADEMARK SAFETY (HARD LINES — APPLY TO EVERY NICHE)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

These rules apply to BOTH the narration (`text`, `full_script`) and the IMAGE PROMPTS.
Image prompts are especially risky — a generator will happily draw a registered trademark
or a recognisable celebrity face. Treat every image_prompt as if a lawyer will review it.

S1 — NO TRADEMARKED LOGOS, BRAND MARKS, MASTHEADS, OR PRODUCT TRADE DRESS:
• Never name a real company logo, app icon, sports-team crest, fashion mark, news-outlet
  masthead, software UI, automobile badge, or product trade-dress in any image_prompt.
• ✗ BANNED in image_prompt: "the Nokia logo on the tower", "front page of the Financial Times",
  "Apple logo glowing", "Coca-Cola can", "Nike swoosh", "the WhatsApp interface", "a Bloomberg
  terminal screen", "the BBC News bug", "a Tesla badge".
• ✓ ALLOWED in image_prompt: "an unbranded modern glass headquarters", "an unnamed broadsheet
  newspaper mid-fold, headline word COLLAPSE visible", "a generic black sedan", "a smartphone
  with a blank dark screen", "an unmarked steel canister", "an institutional newsroom monitor
  showing an unreadable chart".
• Narration may NAME a company factually ("Nokia", "Apple", "the SEC", "BBC") when the story
  is about that entity — the LEGAL fact reporting is fine. The IMAGE must not render the mark.

S2 — LIKENESS / RIGHT OF PUBLICITY:
• Never describe a recognisable likeness of a living public figure in any image_prompt.
  No "a tall thin tech CEO with round glasses and a turtleneck". No "an orange-haired US
  politician". No athlete's signature look. No musician's iconic face.
• Living figures appear ONLY by role + faceless archetype: "back of a head leaving a glass
  building", "hands signing a document", "silhouette at a podium, face out of frame".
• Deceased historical figures: visual-card description is fine. Recently-deceased (last ~25 yrs):
  treat as living — describe by faceless archetype or use period-correct anonymous figures.
• Narration may NAME a living figure when the fact is on the public record. NEVER make
  factual claims about a living private individual that are not already published in a
  mainstream outlet or court filing.

S3 — REAL FOOTAGE / REAL PHOTOGRAPHS / REAL ARTWORK:
• Never instruct the model to "recreate the famous photo of X". Generic compositions are
  fine; named-photograph reconstructions are not (the original photographer holds rights).
• Never name a specific painting / film still / album cover the image should imitate.
• ✗ BAD: "in the style of the Tank Man photograph". ✓ GOOD: describe the composition fresh
  in your own visual grammar.

S4 — MUSIC / LYRICS / FILM QUOTES:
• Never quote song lyrics. Never quote more than a short, attributable, public-record
  factual phrase from a speech. Movie quotes, TV catchphrases, and copyrighted poetry: out.

S5 — MAPS, FLAGS, INSIGNIA — CAREFUL:
• National flags are public-domain symbols and may be described.
• Specific military unit patches, intelligence-agency seals, corporate seals: avoid.
• "An unmarked military convoy" beats "a CIA convoy with the seal on the door".

S6 — DEFAMATION (re-stated for emphasis):
• Never accuse a LIVING named person of a crime they have not been convicted of.
• Never use defamation-by-question ("Did X kill her?") for living unconvicted people.
• Settled, public-record facts about living people are fine. Inferences are not.

S7 — MINORS:
• Never name a minor (under 18 at time of event) in a victim or perpetrator context.
  Use "a 12-year-old boy from Manchester", never the full name.

S8 — RELIGIOUS / CULTURAL FIGURES:
• Living religious leaders: faceless archetype only.
• Sacred imagery: depict respectfully, never as parody.

If a prompt would force the model to render a trademark, a real recent photograph, or a
living-celebrity likeness — rewrite it. There is always a faceless / unbranded version
that hits just as hard. "An unmarked manila folder stamped CLASSIFIED" lands harder than
any agency seal ever would.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RULE U12 — MOTION PROMPTS (Wan 2.2 image-to-video, every clause)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Each clause MUST include ``motion_prompt`` — a separate field from ``image_prompt``.

• ``image_prompt`` = what the STILL FRAME looks like (scene, lighting, subject, shot type).
• ``motion_prompt`` = how that frame ANIMATES (camera move + subject motion + atmosphere).

Wan already receives the Qwen image. Do NOT re-describe the scene in motion_prompt.
Describe ONLY movement inside the existing frame.

Required pattern (20–120 chars, one line, comma-separated):
  [camera move] + [subject/body motion] + [secondary motion / atmosphere shift]

Camera verbs (use at least one):
  push-in, pull-back, dolly, pan, tilt, orbit, tracks, zoom, static shot, handheld

Examples by beat.camera (mirror these in motion_prompt):
  ken_burns  → "camera slow push-in, subject's gaze hardens, dust motes drift in light"
  pan        → "camera slow pan right, banners ripple in wind, smoke curls upward"
  zoom_out   → "camera slow pull-back, subject lowers head, shadows lengthen across frame"
  hold       → "camera static drift, soft breath visible, light shifts slowly across face"
  parallax   → "camera slow orbit, fabric catches breeze, embers float upward"

Match emotion:
  hook / shock     → faster camera, sharper subject reaction
  climactic        → push-in to close-up, sparks or fabric whip
  tragic / reflective → slower drift, settling dust, dimming light
  triumphant       → rising camera, strengthening light beams

✗ BANNED in motion_prompt: scene setup, new characters, text, watermarks, "the image shows"
✓ GOOD: "camera slow push-in, horse mane ripples in wind, dust kicks across foreground"
✗ BAD:  "A Roman general on a battlefield at sunset" (that belongs in image_prompt)

Every clause needs a UNIQUE motion_prompt — never copy-paste the same string twice.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OUTPUT FORMAT — STRICT JSON ONLY (zero markdown, zero commentary)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""


JSON_SHAPE_BLOCK = """\
Return a single JSON object with EXACTLY these top-level keys (no extras, no missing):

{
  "topic": string,
  "cold_open_object": string,
  "decision_lever": {
    "lever_type": "law" | "geography" | "politics" | "technology" | "economics" | "psychology" | "ethics",
    "description": string,
    "consequence": string
  },
  "clauses": [
    {
      "text": string,
      "image_prompt": string,
      "motion_prompt": string,
      "beat": {
        "emotion": "hook"|"tense_buildup"|"suspense"|"reveal"|"triumphant"|"tragic"|"climactic"|"reflective"|"shock",
        "intensity": float,
        "camera": "ken_burns"|"pan"|"zoom_out"|"hold"|"parallax",
        "transition_in": "hard_cut"|"xfade"|"dip_to_black"|"smash_white",
        "duration_hint": "short"|"medium"|"long",
        "color_grade": "epic_warm"|"tragic_cold"|"ancient_sepia"|"dark_thriller"|"golden_hour"|"neon_night"|"clinical_cool"|"forensic_green"|null,
        "audio_event": "none"|"low_rumble"|"impact"|"paper_flutter"|"crowd_cheer"|"sword_clash"|"horse_gallop"|"fire_crackle"|"thunder_crack"|"crowd_murmur"|"phone_ring"|"camera_shutter"|"static"|"heartbeat"|"data_blip",
        "emphasis_words": [string],
        "subtitle_position": "top"|"middle"|"bottom",
        "cut_target": string | null
      }
    }
  ],
  "full_script": string,
  "lut_choice": string,
  "end_plate_question": string
}
"""
