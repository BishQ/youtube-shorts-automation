"""Fallback motion-prompt templates per niche.

The planner SHOULD emit `motion_prompt` on every Clause. When it doesn't (legacy
plans, manual scripts, planner regressions), the I2V stage falls back to one of
these niche templates so we never feed Wan an empty prompt — an empty motion
prompt is the #1 cause of static / jittery I2V output.

Templates describe MOTION, not scene content. The Qwen image already carries
the scene. Each template ends with a deliberately neutral atmosphere clause
so it composes safely with any image_prompt.

Pattern: [camera move] + [subject motion if person] + [secondary motion / atmosphere]
"""

from __future__ import annotations

from shorts_pipeline.planner.schema import EmotionType

# ── Per-niche default motion templates ────────────────────────────────────────
# Keys match the niche directory under src/shorts_pipeline/planner/niches/.
# Pick the one that gives the most cinematic prior for the genre.

_NICHE_DEFAULTS: dict[str, str] = {
    "history": (
        "camera slow push-in, banners ripple in cold wind, dust motes drift in shafts of light, "
        "subject's expression slowly hardens, distant smoke curls upward"
    ),
    "military": (
        "camera slow dolly forward, smoke drifts across the frame, ember sparks float upward, "
        "subject lifts head slowly, fabric and dust catch the wind"
    ),
    "crime": (
        "camera slow push-in to close-up, shadows lengthen across the wall, cigarette smoke curls upward, "
        "rain streaks the window behind, subject's gaze hardens"
    ),
    "cults": (
        "camera slow zoom on face, candles flicker in unison, incense smoke spirals upward, "
        "robes sway slowly, subject's eyes lock onto the lens"
    ),
    "cosmic": (
        "camera slow orbit, stars drift past in parallax, dust lanes swirl in the background, "
        "nebula clouds churn slowly, light bloom pulses faintly"
    ),
    "science": (
        "camera slow push-in, particles drift through volumetric light beams, "
        "subject leans forward, holographic light flickers, dust catches sunlit shaft"
    ),
    "mythology": (
        "camera tilt up from feet to face, sand swirls around the base, "
        "beams of light pierce the ceiling, dust drifts off cracked stone, statue's eyes glow softly"
    ),
    "lost_tech": (
        "camera slow dolly forward through ruins, dust motes drift in golden shafts, "
        "ancient mechanism hums faintly, vines sway, faint light pulses from carvings"
    ),
    "health": (
        "camera slow push-in to extreme close-up, soft breath visible in cool air, "
        "hair strands lift in gentle breeze, eyes slowly open, light shifts warmer"
    ),
    "psychology": (
        "camera slow zoom-in, subject's expression shifts subtly, "
        "background blurs softly, light flickers between warm and cool, dust drifts past"
    ),
    "tech_hackers": (
        "camera slow dolly over shoulder, code reflects on glasses, "
        "monitor glow pulses faintly, server lights blink in background, fingers move across keyboard"
    ),
    "business": (
        "camera slow push-in, papers flutter on the desk, "
        "subject leans forward, curtain billows from window, dust catches sunlight"
    ),
    "survival": (
        "camera handheld micro-shake tracking with subject, snow blows across frame, "
        "breath fogs in cold air, rope sways, sun flares behind ridge"
    ),
    "sports": (
        "camera tracks alongside subject, sweat catches light, "
        "subject's muscles tense, crowd blurs in background, stadium lights flare"
    ),
    "wealth": (
        "camera slow orbit, light glints off polished surfaces, "
        "subject turns slowly toward lens, fabric catches breeze, dust drifts in golden light"
    ),
    "edutainment": (
        "camera slow push-in, subject gestures with hand, "
        "papers flutter, background elements shift gently, light catches the eye"
    ),
    "documentary": (
        "camera slow handheld drift, subject turns toward lens, "
        "ambient particles float, soft light shifts across face, fabric ripples"
    ),
}

# ── Emotion overlays — appended when the planner gave us a beat but no prompt ─
# These nudge the motion toward the emotional register of the clause.

_EMOTION_OVERLAY: dict[EmotionType, str] = {
    EmotionType.hook: "camera fast push-in, snap focus, dust kicks up",
    EmotionType.tense_buildup: "camera slow push-in, breath visible, hands tighten",
    EmotionType.suspense: "camera creeping dolly forward, shadows shift, distant sound bloom",
    EmotionType.reveal: "camera pull-back to reveal, light bloom expands, particles burst outward",
    EmotionType.triumphant: "camera slow rise on subject, banners catch wind, light beams strengthen",
    EmotionType.tragic: "camera slow dolly back, light dims, dust settles, subject lowers head",
    EmotionType.climactic: "camera fast push-in to extreme close-up, sparks erupt, fabric whips",
    EmotionType.reflective: "camera static drift, soft breath, light shifts slowly across face",
    EmotionType.shock: "camera handheld snap to close-up, particles burst, subject's eyes widen",
}

_GENERIC_FALLBACK = (
    "camera slow push-in, subject turns slowly, dust motes drift through soft light, "
    "fabric ripples gently in still air"
)


def default_motion_prompt(
    *,
    niche: str | None,
    emotion: EmotionType | None = None,
) -> str:
    """Return a fallback motion_prompt for a clause that has none.

    Combines the niche-specific base template with an emotion overlay so the
    motion register matches the beat (e.g. a triumphant clause never gets a
    tragic camera move).
    """
    base = _NICHE_DEFAULTS.get((niche or "").lower(), _GENERIC_FALLBACK)
    if emotion is None:
        return base
    overlay = _EMOTION_OVERLAY.get(emotion)
    if not overlay:
        return base
    # Emotion overlay takes the front (camera move) — base provides atmosphere tail.
    # Drop the duplicate camera clause from the base if present.
    base_tail = base.split(",", 1)[1].strip() if "," in base else base
    return f"{overlay}, {base_tail}"
