"""Smart camera-motion director for still-image (Ken Burns) decks.

Generates a per-clip ``camera`` + ``intensity`` sequence that feels hand-edited
instead of a single repeated centre zoom. The output is:

* **Controlled-random** — seeded, so a given job renders the same way every time,
  but each *image* in the deck gets a different move.
* **Non-repetitive** — never the same camera mode twice in a row, and the busy
  motion modes (pan / parallax) are rate-limited so the deck never feels shaky.
* **Breathing** — alternates "push" moves (zoom toward the subject) with
  "release" moves (pull back / glide) so energy rises and settles.
* **Narratively shaped** — a strong push-in on the hook (first clip), a settled
  pull-back/hold on the resolution (last clip), and emotion-aware bias in
  between when the planner provides real emotions.

This is used by the editor when the plan's beats are flat/default (every clause
sharing one camera). Plans that already carry deliberate per-beat camera variety
are left untouched.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from shorts_pipeline.planner.schema import CameraMotion, EmotionType

# Cameras grouped by the energy they convey, so we can alternate the two groups.
_PUSH: tuple[CameraMotion, ...] = (CameraMotion.ken_burns, CameraMotion.parallax)
_RELEASE: tuple[CameraMotion, ...] = (
    CameraMotion.zoom_out,
    CameraMotion.pan,
    CameraMotion.hold,
)

# The most motion-heavy modes — limited to avoid a jittery, over-animated deck.
_BUSY: frozenset[CameraMotion] = frozenset({CameraMotion.parallax, CameraMotion.pan})

# Tasteful intensity envelope. We never go fully calm or fully aggressive.
_INTENSITY_MIN = 0.40
_INTENSITY_MAX = 0.75
_INTENSITY_HOOK_FLOOR = 0.66
_INTENSITY_SETTLE_CEIL = 0.52

_PUSH_EMOTIONS = frozenset(
    {
        EmotionType.hook,
        EmotionType.shock,
        EmotionType.climactic,
        EmotionType.triumphant,
        EmotionType.tense_buildup,
    }
)
_RELEASE_EMOTIONS = frozenset({EmotionType.reflective, EmotionType.tragic})


@dataclass(frozen=True)
class ClipMotion:
    """One clip's resolved camera move."""

    camera: CameraMotion
    intensity: float


def _group_of(camera: CameraMotion) -> str:
    return "push" if camera in _PUSH else "release"


def _emotion_group(emotion: EmotionType | None) -> str | None:
    if emotion in _PUSH_EMOTIONS:
        return "push"
    if emotion in _RELEASE_EMOTIONS:
        return "release"
    return None


def _pick_intensity(
    rng: random.Random,
    *,
    is_hook: bool,
    is_settle: bool,
    emotion: EmotionType | None,
) -> float:
    value = rng.uniform(_INTENSITY_MIN, _INTENSITY_MAX)
    if is_hook or emotion in (EmotionType.hook, EmotionType.shock, EmotionType.climactic):
        value = max(value, _INTENSITY_HOOK_FLOOR)
    if is_settle or emotion in (EmotionType.reflective, EmotionType.tragic):
        value = min(value, _INTENSITY_SETTLE_CEIL)
    return round(value, 3)


def direct_clip_motion(
    clip_count: int,
    *,
    seed: int | None = None,
    emotions: list[EmotionType | None] | None = None,
) -> list[ClipMotion]:
    """Return a smart, controlled-random ``ClipMotion`` per clip.

    Parameters
    ----------
    clip_count:
        Number of clips (images) in the deck.
    seed:
        RNG seed. Pass a job-derived seed for reproducible-yet-varied output;
        pass ``None`` for a fresh random sequence each call.
    emotions:
        Optional per-clip emotion tags. When present they bias the camera group
        (e.g. shock → push-in, reflective → pull-back) without breaking the
        non-repeat / breathing rules.
    """
    if clip_count <= 0:
        return []

    rng = random.Random(seed)
    out: list[ClipMotion] = []
    prev_camera: CameraMotion | None = None
    prev_group: str | None = None
    busy_streak = 0

    for i in range(clip_count):
        emotion = emotions[i] if emotions is not None and i < len(emotions) else None
        is_hook = i == 0
        is_settle = i == clip_count - 1 and clip_count > 1

        if is_hook:
            # Open on a confident push-in toward the subject.
            camera = CameraMotion.ken_burns
        elif is_settle:
            # Land on an exhale — pull back or a breathing hold.
            settle_choices = [c for c in (CameraMotion.zoom_out, CameraMotion.hold) if c != prev_camera]
            camera = rng.choice(settle_choices or [CameraMotion.zoom_out])
        else:
            bias = _emotion_group(emotion)
            if bias is not None:
                group_name = bias
            else:
                # Alternate groups so the deck keeps breathing push → release.
                group_name = "release" if prev_group == "push" else "push"
            group = _PUSH if group_name == "push" else _RELEASE

            candidates = [c for c in group if c != prev_camera]
            if busy_streak >= 1:
                calm = [c for c in candidates if c not in _BUSY]
                candidates = calm or candidates
            if not candidates:
                candidates = [c for c in group]
            camera = rng.choice(candidates)

        intensity = _pick_intensity(
            rng,
            is_hook=is_hook,
            is_settle=is_settle,
            emotion=emotion,
        )
        out.append(ClipMotion(camera=camera, intensity=intensity))

        busy_streak = busy_streak + 1 if camera in _BUSY else 0
        prev_camera = camera
        prev_group = _group_of(camera)

    return out
