"""FFmpeg ``xfade`` transition catalog and helpers.

Every name here matches ``ffmpeg -h filter=xfade`` (excluding ``custom``).
Durations are defaults before the renderer clamps to adjacent clip lengths.
"""

from __future__ import annotations

import random

from shorts_pipeline.editor import emotion_to_filtergraph as _em
from shorts_pipeline.editor.models import ClipSpec

# All built-in xfade transitions (int 0..57), excluding ``custom`` (-1).
XFADE_EFFECT_NAMES: tuple[str, ...] = (
    "fade",
    "wipeleft",
    "wiperight",
    "wipeup",
    "wipedown",
    "slideleft",
    "slideright",
    "slideup",
    "slidedown",
    "circlecrop",
    "rectcrop",
    "distance",
    "fadeblack",
    "fadewhite",
    "radial",
    "smoothleft",
    "smoothright",
    "smoothup",
    "smoothdown",
    "circleopen",
    "circleclose",
    "vertopen",
    "vertclose",
    "horzopen",
    "horzclose",
    "dissolve",
    "pixelize",
    "diagtl",
    "diagtr",
    "diagbl",
    "diagbr",
    "hlslice",
    "hrslice",
    "vuslice",
    "vdslice",
    "hblur",
    "fadegrays",
    "wipetl",
    "wipetr",
    "wipebl",
    "wipebr",
    "squeezeh",
    "squeezev",
    "zoomin",
    "fadefast",
    "fadeslow",
    "hlwind",
    "hrwind",
    "vuwind",
    "vdwind",
    "coverleft",
    "coverright",
    "coverup",
    "coverdown",
    "revealleft",
    "revealright",
    "revealup",
    "revealdown",
)

# Tuned defaults (seconds). Anything omitted uses _DEFAULT_XFADE_S.
_DEFAULT_XFADE_S = 0.36
_EFFECT_DURATION_S: dict[str, float] = {
    "fadefast": 0.22,
    "fadeslow": 0.55,
    "fadeblack": 0.48,
    "fadewhite": 0.42,
    "fadegrays": 0.48,
    "dissolve": 0.42,
    "pixelize": 0.40,
    "hblur": 0.45,
    "zoomin": 0.34,
    "squeezeh": 0.32,
    "squeezev": 0.32,
    "circleopen": 0.44,
    "circleclose": 0.44,
    "radial": 0.40,
    "distance": 0.40,
}


def default_duration_for_xfade_effect(name: str) -> float:
    return _EFFECT_DURATION_S.get(name, _DEFAULT_XFADE_S)


def pick_random_xfade_effect(rng: random.Random) -> str:
    return rng.choice(XFADE_EFFECT_NAMES)


def resolve_xfade_for_clip(clip: ClipSpec) -> tuple[str, float]:
    """Return FFmpeg ``transition=`` name and base duration (seconds).

    For ``hard_cut`` the name is a placeholder and duration is ``0.0``.
    """
    from shorts_pipeline.planner.schema import TransitionType

    trans = clip.transition_in
    if trans == TransitionType.hard_cut:
        return ("fade", 0.0)
    if clip.xfade_effect_name:
        n = clip.xfade_effect_name
        if n not in XFADE_EFFECT_NAMES:
            # Defensive: bad serialized plan → safe crossfade
            return ("fade", _DEFAULT_XFADE_S)
        return (n, default_duration_for_xfade_effect(n))
    return (_em.XFADE_TRANSITION[trans], _em.XFADE_DURATION[trans])
