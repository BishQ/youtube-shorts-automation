"""Structured narration plan with beat metadata. Retention hooks enforced here."""

from __future__ import annotations

import re
from difflib import get_close_matches
from enum import StrEnum
from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic import ValidationInfo

# Body narration only; 1–3 s of the 60 s Shorts cap reserved for end plate / breathe.
# Per-niche caps live in ``niche_caps.NICHE_CAPS`` and are passed in via the
# validator context (``allow_figure_name``-style). These module-level numbers
# are the **fallback** used when no niche is supplied — they are deliberately
# conservative (set to the all-niche median observed in calibration).
from shorts_pipeline.planner.niche_caps import (
    DEFAULT_MAX_SYLLABLES as _DEFAULT_MAX_SYLLABLES,
    DEFAULT_MAX_WORDS as _DEFAULT_MAX_WORDS,
    MIN_WORDS as NARRATION_SCRIPT_MIN_WORDS,
    caps_for as _caps_for,
    count_syllables as _count_syllables,
)

# Kept as a module-level name for backward compatibility with callers that
# `from schema import NARRATION_SCRIPT_MAX_WORDS`. New code should call
# ``caps_for(niche)`` or pass the niche via ValidationInfo context.
NARRATION_SCRIPT_MAX_WORDS = _DEFAULT_MAX_WORDS


class DecisionLeverType(StrEnum):
    law = "law"
    geography = "geography"
    politics = "politics"


class DecisionLever(BaseModel):
    lever_type: DecisionLeverType
    description: str = Field(..., min_length=8)
    consequence: str = Field(..., min_length=8)


# ── Beat enums ────────────────────────────────────────────────────────────────

class EmotionType(StrEnum):
    hook = "hook"
    tense_buildup = "tense_buildup"
    suspense = "suspense"
    reveal = "reveal"
    triumphant = "triumphant"
    tragic = "tragic"
    climactic = "climactic"
    reflective = "reflective"
    shock = "shock"


class CameraMotion(StrEnum):
    ken_burns = "ken_burns"   # slow zoom-in (default)
    pan = "pan"               # horizontal pan
    zoom_out = "zoom_out"     # start wide, pull back
    hold = "hold"             # barely-perceptible drift
    parallax = "parallax"     # zoom + lateral drift — simulated depth on a still


class TransitionType(StrEnum):
    hard_cut = "hard_cut"             # instant (no blend)
    xfade = "xfade"                   # 0.3 s cross-fade
    dip_to_black = "dip_to_black"     # 0.4 s fade through black
    smash_white = "smash_white"       # 0.1 s flash to white
    paint_splatter = "paint_splatter" # organic dissolve
    vr_light_rays = "vr_light_rays"   # bright white-flash burst
    random_blocks = "random_blocks"   # block-pixelization wipe
    center_split = "center_split"     # horizontal split from center


class DurationHint(StrEnum):
    short = "short"    # ~0.7 s  (hook zone)
    medium = "medium"  # ~2.8 s  (body)
    long = "long"      # ~5.0 s  (close)


class ColorGrade(StrEnum):
    epic_warm = "epic_warm"
    tragic_cold = "tragic_cold"
    ancient_sepia = "ancient_sepia"
    dark_thriller = "dark_thriller"
    golden_hour = "golden_hour"


class AudioEvent(StrEnum):
    none = "none"
    # Generic / ambient
    low_rumble = "low_rumble"       # ominous tension, slow dread building
    impact = "impact"               # single hard hit, explosion, door slam
    paper_flutter = "paper_flutter" # documents, maps, scrolls, letters
    # Scene-matched
    crowd_cheer = "crowd_cheer"     # triumphant mass scenes, kneeling armies, arenas
    sword_clash = "sword_clash"     # blade combat, battle, assassination attempts
    horse_gallop = "horse_gallop"   # cavalry charge, riding across steppe/battlefield
    fire_crackle = "fire_crackle"   # burning city, torches, execution pyres
    thunder_crack = "thunder_crack" # climax reveal, smash_white transition, shock beats
    crowd_murmur = "crowd_murmur"   # senate/court intrigue, whispered conspiracy, suspense


class SubtitlePosition(StrEnum):
    top = "top"
    middle = "middle"
    bottom = "bottom"


class VisualTier(StrEnum):
    """Visual stylization tier for the image_prompt.

    The 70/20/10 contrast law: most frames should be grounded so the rare
    legendary frames actually land. Stacking blockbuster style on every frame
    is the canonical 'AI slideshow' tell.
    """
    grounded = "grounded"     # documentary realism, available light, no atmosphere stack
    cinematic = "cinematic"   # one elevation element (harsh light OR shadow OR DoF)
    legendary = "legendary"   # full blockbuster: god-rays + scale + particles


class LutChoice(StrEnum):
    epic_warm = "epic_warm"
    tragic_cold = "tragic_cold"
    ancient_sepia = "ancient_sepia"
    dark_thriller = "dark_thriller"
    golden_hour = "golden_hour"


# ── Beat ──────────────────────────────────────────────────────────────────────

def _coerce_enum(value: object, choices: list[str]) -> object:
    """Return closest valid enum string for near-misses; leave others untouched."""
    if not isinstance(value, str):
        return value
    if value in choices:
        return value
    matches = get_close_matches(value, choices, n=1, cutoff=0.6)
    return matches[0] if matches else value


class Beat(BaseModel):
    emotion: EmotionType = EmotionType.reflective
    intensity: float = Field(default=0.5, ge=0.0, le=1.0)
    camera: CameraMotion = CameraMotion.ken_burns
    transition_in: TransitionType = TransitionType.xfade
    duration_hint: DurationHint = DurationHint.medium
    color_grade: ColorGrade | None = None
    audio_event: AudioEvent = AudioEvent.none
    emphasis_words: list[str] = Field(default_factory=list)
    # Visual stylization tier (70/20/10 contrast law).
    # Default grounded — restraint is the prior, elevation is the exception.
    visual_tier: VisualTier = VisualTier.grounded

    @model_validator(mode="before")
    @classmethod
    def coerce_enums(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        _emotion   = [e.value for e in EmotionType]
        _camera    = [e.value for e in CameraMotion]
        _transition= [e.value for e in TransitionType]
        _duration  = [e.value for e in DurationHint]
        _color     = [e.value for e in ColorGrade]
        _audio     = [e.value for e in AudioEvent]
        _tier      = [e.value for e in VisualTier]
        for field, choices in (
            ("emotion",     _emotion),
            ("camera",      _camera),
            ("transition_in", _transition),
            ("duration_hint", _duration),
            ("audio_event", _audio),
            ("visual_tier", _tier),
        ):
            if field in data:
                data[field] = _coerce_enum(data[field], choices)
        if "color_grade" in data and data["color_grade"] is not None:
            data["color_grade"] = _coerce_enum(data["color_grade"], _color)
        return data
    subtitle_position: SubtitlePosition = SubtitlePosition.bottom
    # Word string in clause text that the cut should snap to (first word if None)
    cut_target: str | None = None


# ── Clause + Plan ─────────────────────────────────────────────────────────────

_IMG_BANNED = re.compile(
    # "then " alone matched legitimate prose ("cool light, then a rim hit"); keep
    # transformation-gimmick phrases via then-and-now / before-and-after / morphing.
    r'\b(swastika|nazi flag|ss uniform|then\s+and\s+now|transitioning|morphing|split.?screen'
    r'|before and after|collage|comic.?panel|manga.?panel|split.?panel'
    # NOTE: do not ban bare "multi panel" / "multi-panel" — concert/stadium LED walls
    # and stage rigs use that wording legitimately; comic misuse is covered by comic.?panel
    # and panel.?grid.
    r'|panel.?grid|smartphone|\bcellphones?\b|\bcell phones?\b|\bmobile phones?\b|\biphones?\b'
    r'|laptop|neon sign|skyscraper'
    # ── Platform safety: blood / gore / graphic violence ─────────────────────
    # These terms cause image generators to output content that gets flagged or
    # removed by YouTube, TikTok, and Instagram moderation systems.
    r'|blood.?soaked|blood.?drenched|blood.?splattered|blood.?covered|blood.?stained'
    r'|soaked.?in.?blood|pool.?of.?blood|puddle.?of.?blood|river.?of.?blood'
    r'|gushing.?blood|blood.?gushing|blood.?pouring|blood.?dripping|blood.?running'
    r'|blood.?flowing|bleeding.?profusely|blood.?everywhere|drenched.?in.?blood'
    r'|severed.?head|severed.?limb|severed.?arm|severed.?leg|severed.?hand|severed.?neck'
    r'|decapitat|dismember|behead|mutilat'
    r'|dead.?body|rotting|decomposing|festering'
    r'|gore\b|gory|entrails|guts\b|viscera|intestin|organ.?spill'
    r'|torture.?rack|(?:being )?tortured on (?:a|the) rack\b|graphic.?wound|open.?wound|exposed.?bone'
    r'|mass.?execution|graphic.?death)\b',
    re.IGNORECASE,
)
_SHOT_TYPES = re.compile(
    r'\b('
    r'close.?up|wide shot|medium shot|low.angle|high.angle|dutch angle|establishing'
    r'|over.?the.?shoulder|profile silhouette|reverse angle|god.?s.?eye'
    r'|rack.?focus|hero shot|hero portrait|hero close|tight (?:close|portrait|shot)'
    r'|extreme (?:close|wide)|portrait shot|tracking shot|aerial shot|top.?down'
    r')\b',
    re.IGNORECASE,
)
_LIGHTING = re.compile(
    r'(light|shadow|candle|backlit|silhouette|lamp|torch|dawn|dusk|overcast'
    r'|glow|gleam|dim|bright|flicker|sunlit|moonlit|spotlight|torchlit|haze|mist'
    r'|chiaroscuro|volumetric|fluorescent|tungsten|diffused|ambient|sunrise|sunset'
    r'|twilight|midday|noon|harsh|soft.?light|natural.?light|blue.hour|golden.hour'
    r'|low.?key|high.?key|contre.?jour)',
    re.IGNORECASE,
)
# Camera-move verbs accepted in motion_prompt — kept narrow so the LLM doesn't
# describe scene content here (that lives in image_prompt). The image is given;
# motion_prompt describes how the camera/subject MOVES inside it.
_MOTION_CAMERA = re.compile(
    r'\b('
    r'push.?in|pull.?out|pull.?back|dolly|tracks?|tracking|orbit|orbits?'
    r'|pan(?:s|ning)?|tilt(?:s|ing)?|crane|booms?|booming|zooms?|zooming'
    r'|rotates?|rotating|rises?|rising|descends?|descending|drifts?|drifting'
    r'|static shot|locked.?off|handheld|micro.?shake'
    r')\b',
    re.IGNORECASE,
)

_HUMAN_REFERENCE = re.compile(
    r'\b('
    r'face|eye|eyes|hand|hands|finger|fingers|arm|arms|leg|legs|knee|knees'
    r'|shoulder|shoulders|chest|back|head|hair|brow|jaw|lip|lips|mouth|teeth'
    r'|profile|silhouette|body|figure|portrait|self.?portrait'
    r'|man|woman|boy|girl|child|children|baby|infant|youth|teen|teenager'
    r'|elder|old.?man|old.?woman|warrior|king|queen|emperor|empress|priest'
    r'|priestess|soldier|general|commander|chieftain|chief|rider|horseman'
    r'|horsewoman|scribe|scholar|peasant|noble|servant|guard|guards'
    r'|warriors|soldiers|riders|men|women|crowd|figures|people'
    r'|athlete|player|boxer|fighter|runner|swimmer|champion|icon|hero|legend'
    r')\b',
    re.IGNORECASE,
)

# Face-related tokens for the clause-1 hero portrait requirement.
# clauses[0].image_prompt MUST contain at least one of these — hand/silhouette alone fails.
_FACE_REFERENCE = re.compile(
    r'\b('
    r'face|eye|eyes|gaze|stare|jaw|brow|brows|lip|lips|mouth|teeth|cheek|cheeks'
    r'|chin|forehead|nose|ear|ears|expression|profile|portrait|hero (?:close.?up|shot|portrait)'
    r')\b',
    re.IGNORECASE,
)

# Phrases that signal AI/trailer cliché writing — auto-fail if present in narration.
_BANNED_PHRASES = re.compile(
    r'\b('
    r'rose to power|changed history|shaped the world|crumbled'
    r'|still echoes today|would echo through the centuries'
    r'|would change the world forever|a name that would live on'
    r'|a force to be reckoned with|his rise was unstoppable'
    r'|the legal document that changed everything|his world fractured'
    r'|rivers of blood|viper.?s nest|friend or foe'
    r'|the rest is history|for better or worse'
    r')\b',
    re.IGNORECASE,
)

# Curiosity-gap question-words that signal a hook of the right shape.
_CURIOSITY_GAP_OPENER = re.compile(
    r'^\s*("?)(how|why|what|who)\b',
    re.IGNORECASE,
)

# Biographical birth/death-year patterns used in CLOSING clauses (12-14).
# Opening clauses may use them for viewer orientation; closing clauses must
# end on a powerful image/reframe — not a biography footnote.
_CLOSING_BIO_YEAR_RE = re.compile(
    r'\b(born\s+in\s+\d{1,4}|died\s+in\s+\d{1,4})\s*(BCE?|CE|AD)?\b',
    re.IGNORECASE,
)


class Clause(BaseModel):
    text: str = Field(..., min_length=4, description="Single narration clause")
    image_prompt: str = Field(..., min_length=40)
    # Image-to-video motion prompt for Wan 2.2 I2V (or any I2V backend).
    # The image already describes scene content — motion_prompt describes how
    # the camera/subject MOVES inside it. Format:
    #   [subject action] + [camera move] + [secondary motion] + [atmosphere shift]
    # Empty string → use niche-default template at I2V time (see video_worker.motion_templates).
    motion_prompt: str = Field(default="", description="I2V motion description: camera move + subject action + atmosphere")
    beat: Beat = Field(default_factory=Beat)
    # True  → historical figure appears in this image (use Grok for likeness)
    # False → figure-free scene: city, landscape, object, crowd without the figure
    figure_present: bool = True

    @field_validator("motion_prompt")
    @classmethod
    def validate_motion_prompt(cls, v: str) -> str:
        if not v:
            return v  # empty allowed — fallback template applied at I2V time
        if len(v) < 20:
            raise ValueError(
                f"motion_prompt too short ({len(v)} chars). Must describe camera move + "
                f"secondary motion. Example: 'camera slow push-in, dust motes drift in shafts of light, "
                f"subject slowly turns head'. Got: {v[:80]}"
            )
        if not _MOTION_CAMERA.search(v):
            raise ValueError(
                "motion_prompt must include a camera-move verb "
                "(push-in / pull-out / dolly / pan / tilt / orbit / tracks / zoom / "
                "static shot / handheld). Describe MOTION, not scene content — the image "
                f"already carries the scene. Got: {v[:120]}"
            )
        if _IMG_BANNED.search(v):
            raise ValueError(f"motion_prompt contains banned content: {v[:120]}")
        return v

    @field_validator("image_prompt")
    @classmethod
    def validate_image_prompt(cls, v: str) -> str:
        m_ban = _IMG_BANNED.search(v)
        if m_ban:
            lo = max(0, m_ban.start() - 24)
            hi = min(len(v), m_ban.end() + 24)
            snippet = v[lo:hi].replace("\n", " ")
            raise ValueError(
                "image_prompt contains banned content "
                f"({m_ban.group(0)!r} in …{snippet}…): {v[:120]}"
            )
        if not _SHOT_TYPES.search(v):
            raise ValueError(f"image_prompt missing shot type (close-up/wide/medium etc): {v[:120]}")
        if not _LIGHTING.search(v):
            raise ValueError(f"image_prompt missing lighting description: {v[:120]}")
        return v


class NarrationPlan(BaseModel):
    historical_figure: str = Field(..., min_length=2)
    cold_open_object: str = Field(
        ...,
        min_length=4,
        description="Physical object tied to the cold open (coin, tablet, map, weapon, etc.)",
    )
    decision_lever: DecisionLever
    clauses: list[Clause] = Field(..., min_length=14, max_length=14)
    full_script: str = Field(..., min_length=20)
    lut_choice: LutChoice = LutChoice.epic_warm
    end_plate_question: str = Field(
        default="What would YOU have done?",
        min_length=4,
        description="Loop-hook question shown on the end plate",
    )
    # Optional niche tag (history, crime, …) for I2V motion fallbacks and TTS caps.
    niche: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def script_coherent(self, info: ValidationInfo) -> NarrationPlan:
        joined = " ".join(c.text.strip() for c in self.clauses)
        if len(joined) < len(self.full_script) * 0.5:
            raise ValueError("clauses do not cover most of full_script; keep clauses aligned to narration")

        # Per-niche caps are passed in via ValidationInfo context ({"niche": "history"}).
        # When no niche is supplied (legacy callers / ad-hoc plan validation),
        # ``caps_for`` returns the conservative defaults.
        niche = (info.context or {}).get("niche") if isinstance(info.context, dict) else None
        min_words, max_words, max_syllables = _caps_for(niche)

        word_count = len(self.full_script.split())
        if word_count < min_words:
            raise ValueError(
                f"full_script is only {word_count} words — minimum is {min_words} words "
                "(too short for the Shorts body window). Target "
                f"{min_words}–{max_words} words across exactly 14 clauses for a 58-59 sec body."
            )
        if word_count > max_words:
            raise ValueError(
                f"full_script is {word_count} words — exceeds the niche {niche!r} word "
                f"limit of {max_words}. This cap was calibrated from real Kokoro TTS "
                "runs on this niche's vocabulary. Body must fit in 58-59 sec; outro "
                f"takes 1-2 sec of the 60 sec cap. Keep between {min_words}–{max_words} "
                "words across exactly 14 clauses."
            )

        # Syllable gate — the true TTS load. Word count alone is a weak predictor of
        # duration (R²=0.21 in calibration) because vocabulary density varies wildly
        # between niches. Syllable count predicts duration nearly linearly (R²=0.86).
        # If this gate fails the script is over-syllabified for its niche even when
        # the word count is fine (typical failure mode: Latin titles, polysyllabic
        # technical jargon, hyphenated compound nouns).
        syll_count = _count_syllables(self.full_script)
        if syll_count > max_syllables:
            raise ValueError(
                f"full_script contains {syll_count} syllables — exceeds the niche "
                f"{niche!r} syllable budget of {max_syllables}. Even with the right "
                "word count, dense polysyllabic vocabulary blows the 58 s TTS window "
                "(Kokoro reads ~4.5 syllables/sec). Replace long Latinate/technical "
                "words with shorter plain-English equivalents. Examples: "
                "'characteristics' → 'traits', 'demonstration' → 'proof', "
                "'logarithmic' → 'logs', 'extraordinarily' → 'incredibly'."
            )

        # Closing bio-year ban: clauses 12-14 (indices 11-13) must end on a powerful
        # reframe/image — NOT a biography footnote like "born in X" or "died in Y".
        # Opening clauses may use birth/death year for viewer orientation.
        for idx in range(11, len(self.clauses)):
            clause_text = self.clauses[idx].text
            m = _CLOSING_BIO_YEAR_RE.search(clause_text)
            if m:
                raise ValueError(
                    f"clauses[{idx}].text (closing/resonance section) contains a biographical "
                    f"year pattern: '{m.group()[:40]}'. "
                    "Closing clauses (12-14) must end on a concrete image or reframe — "
                    "not a biography block. Remove the year; keep the fact if it carries drama. "
                    f"Example: 'He died in 1227' → 'He died. His soldiers killed every witness.'"
                )

        first_clause_text = self.clauses[0].text if self.clauses else ""
        first_sentence = re.split(r'[.!]', first_clause_text, maxsplit=1)[0].strip()
        if "?" not in first_sentence:
            raise ValueError(
                "clauses[0].text MUST begin with a curiosity-gap question that ends with '?' "
                "BEFORE the first period. The question is the hook — viewers decide in 3 seconds. "
                "Pattern: 'How does a [vulnerable-version] become [final-form]?' "
                "or 'Why would [respected group] kneel to [unlikely person]?' "
                "or 'What does it cost to [seemingly-noble outcome]?'. "
                f"Current first sentence: {first_sentence[:140]}"
            )
        if not _CURIOSITY_GAP_OPENER.search(first_clause_text):
            raise ValueError(
                "clauses[0].text must START with a curiosity-gap question word "
                "(How / Why / What / Who) — never a yes/no question, never a statement. "
                f"Current opening: {first_clause_text[:80]}"
            )

        body_question_marks = sum(c.text.count("?") for c in self.clauses[1:])
        first_clause_question_marks = first_clause_text.count("?")
        if first_clause_question_marks > 1:
            raise ValueError(
                f"clauses[0].text contains {first_clause_question_marks} question marks. "
                "Exactly 1 hook question is allowed in clause 1 (the first sentence). "
                "Remove any additional questions."
            )
        if body_question_marks > 0:
            offending = [
                f"clauses[{i + 1}]: {c.text[:80]}"
                for i, c in enumerate(self.clauses[1:])
                if "?" in c.text
            ]
            raise ValueError(
                f"Found {body_question_marks} question mark(s) inside clauses[1:]. "
                "ZERO question marks are allowed in any clause after clause 1. "
                "Only 2 questions exist in the entire output: clause 1 hook + end_plate_question. "
                f"Offending clauses: {'; '.join(offending)}"
            )
        if self.end_plate_question.count("?") != 1:
            raise ValueError(
                f"end_plate_question must contain exactly 1 question mark. "
                f"Got {self.end_plate_question.count('?')}. Current: {self.end_plate_question[:120]}"
            )

        first_image = self.clauses[0].image_prompt if self.clauses else ""
        if not _HUMAN_REFERENCE.search(first_image):
            raise ValueError(
                "clauses[0].image_prompt must establish the historical figure visually "
                "(face, hand, eye, silhouette, child version of the figure, etc.) — "
                "never the cold_open_object alone in an empty frame. "
                f"Current first image: {first_image[:140]}"
            )
        if not _FACE_REFERENCE.search(first_image):
            raise ValueError(
                "clauses[0].image_prompt must be a HERO PORTRAIT — the historical figure's "
                "FACE (or face-related feature: eyes, jaw, brow, lips, mouth, profile, "
                "expression, gaze) must be the dominant element. Hand-only, silhouette-only, "
                "or environment-only first images are rejected. The face on screen must be "
                "the visual answer to the hook question. "
                f"Current first image: {first_image[:160]}"
            )

        banned_in_script = _BANNED_PHRASES.findall(self.full_script)
        if banned_in_script:
            raise ValueError(
                f"full_script contains banned cliché phrases: {banned_in_script}. "
                "These are AI-cliché tells. Replace each with a specific, concrete, "
                "historically-grounded image or fact."
            )
        for i, clause in enumerate(self.clauses):
            banned_in_clause = _BANNED_PHRASES.findall(clause.text)
            if banned_in_clause:
                raise ValueError(
                    f"clauses[{i}].text contains banned cliché phrases: {banned_in_clause}. "
                    "Rewrite this clause with a specific, concrete fact."
                )

        # ── Soft tier guard — only catches extreme stacking (≥12 legendary, all 14 at 0.9+).
        # The 70/20/10 rule is a SOFT guide in the prompt now, not a hard validator.
        # User preference: keep visual production value high; image quality is the priority.
        # Only block the truly pathological cases (every single frame screaming).
        skip_tier_check = bool((info.context or {}).get("skip_tier_check", False))
        if not skip_tier_check:
            n = len(self.clauses)
            n_legendary = sum(1 for c in self.clauses if c.beat.visual_tier == VisualTier.legendary)
            # Allow up to ~half of frames as legendary — only block extreme cases.
            if n_legendary > max(7, n // 2 + 1):
                raise ValueError(
                    f"{n_legendary}/{n} clauses are visual_tier=legendary — too many. "
                    "Even at maximum production value, at least a few frames must be cinematic "
                    "or grounded so the legendary peaks have impact to land into. "
                    f"Demote at least {n_legendary - 7} clauses to cinematic."
                )
            # Block ALL 14 beats sitting at peak intensity (≥0.9) — the curve must breathe.
            n_peak = sum(1 for c in self.clauses if c.beat.intensity >= 0.9)
            if n_peak > 8:
                raise ValueError(
                    f"{n_peak}/{n} clauses have intensity ≥ 0.9. The emotional curve must "
                    "breathe — peaks need valleys. Drop at least some clauses below 0.75."
                )

        # ── Name-in-image-prompt guard ─────────────────────────────────────────
        # Flux/ComfyUI ignore real names; Grok renders them accurately.
        # Skip this check when the caller passes context={"allow_figure_name": True}.
        allow_figure_name = bool((info.context or {}).get("allow_figure_name", False))
        if not allow_figure_name:
            name_parts = [
                p.strip().lower()
                for p in re.split(r"[\s\-]+", self.historical_figure)
                if len(p.strip()) > 2
            ]
            for i, clause in enumerate(self.clauses):
                prompt_lower = clause.image_prompt.lower()
                found_parts = [p for p in name_parts if p in prompt_lower]
                if len(found_parts) >= 2:
                    raise ValueError(
                        f"clauses[{i}].image_prompt contains the figure's real name "
                        f"({found_parts}). Image generators ignore names and generate the "
                        f"WRONG PERSON (e.g. writing 'Kareem Abdul-Jabbar' in a Lakers scene "
                        f"generates Kobe instead). "
                        f"Replace the name with the figure's UNIQUE PHYSICAL SIGNATURE: "
                        f"height, jersey number, signature move, distinctive clothing/accessory. "
                        f"Example: instead of 'Kareem Abdul-Jabbar mid-skyhook' write "
                        f"'a towering 7-foot-2 player in Lakers #33 jersey, protective goggles, "
                        f"mid-skyhook release with arm impossibly high'."
                    )

        return self


_NON_ASCII_RE = re.compile(r"[^\x00-\x7F]")
_ASCII_RE = re.compile(r"[\x00-\x7F]")

# Mongolian Cyrillic block: U+0400–U+04FF (covers standard Mongolian alphabet)
_MONGOLIAN_CYRILLIC_RE = re.compile(r"[\u0400-\u04FF]")


def validate_english_figure_v1(plan: NarrationPlan, *, topic_type: str, language: str) -> None:
    if topic_type != "historical_figure":
        raise ValueError("V1 only supports topic_type=historical_figure")
    if language != "en":
        raise ValueError("V1 only supports language=en")
    blob = plan.full_script + " " + " ".join(c.text for c in plan.clauses)
    non_ascii = len(_NON_ASCII_RE.findall(blob))
    if non_ascii > max(3, len(blob) // 200):
        raise ValueError("English-only check failed: too many non-ASCII characters in script")


def validate_mongolian_figure_v1(plan: NarrationPlan, *, topic_type: str, language: str) -> None:
    """Validate a plan generated for Mongolian-language narration (language='mn').

    Rules:
    • full_script and clause.text must contain Mongolian Cyrillic characters.
    • image_prompts must stay predominantly ASCII/English (image-generator requirement).
    • topic_type must be historical_figure.
    """
    if topic_type != "historical_figure":
        raise ValueError("Mongolian V1 only supports topic_type=historical_figure")
    if language != "mn":
        raise ValueError("validate_mongolian_figure_v1 requires language=mn")

    # Narration text must contain Mongolian Cyrillic
    narration_blob = plan.full_script + " " + " ".join(c.text for c in plan.clauses)
    cyrillic_count = len(_MONGOLIAN_CYRILLIC_RE.findall(narration_blob))
    if cyrillic_count < max(10, len(narration_blob) // 20):
        raise ValueError(
            "Mongolian language check failed: full_script and clause.text must be written "
            "in Mongolian Cyrillic script. Found too few Cyrillic characters."
        )

    # image_prompts must stay in English — Cyrillic in image prompts breaks image generators
    for i, clause in enumerate(plan.clauses):
        cyrillic_in_image = len(_MONGOLIAN_CYRILLIC_RE.findall(clause.image_prompt))
        ascii_in_image = len(_ASCII_RE.findall(clause.image_prompt))
        if cyrillic_in_image > 0 and cyrillic_in_image > ascii_in_image * 0.1:
            raise ValueError(
                f"clauses[{i}].image_prompt contains Mongolian Cyrillic text. "
                "image_prompts MUST be written in English — the image generator "
                "does not understand Mongolian. Write the narration in Mongolian "
                "but keep all image_prompt values in English."
            )


# Registry mapping language codes to their validator functions
_VALIDATORS = {
    "en": validate_english_figure_v1,
    "mn": validate_mongolian_figure_v1,
}


def validate_figure_plan(plan: NarrationPlan, *, topic_type: str, language: str) -> None:
    """Dispatch to the correct per-language validator."""
    validator = _VALIDATORS.get(language)
    if validator is None:
        raise ValueError(
            f"Unsupported language {language!r}. "
            f"Supported languages: {list(_VALIDATORS.keys())}"
        )
    validator(plan, topic_type=topic_type, language=language)
