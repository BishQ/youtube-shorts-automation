"""Resolve the Wan I2V motion prompt for a clause.

Priority:
  1. ``clause.motion_prompt`` when the planner wrote a valid prompt.
  2. Beat-aware synthesis: ``beat.camera`` + ``beat.emotion`` + niche atmosphere.
  3. Generic cinematic fallback.
"""

from __future__ import annotations

import re

from shorts_pipeline.planner.schema import CameraMotion, Clause, EmotionType
from shorts_pipeline.video_worker.motion_templates import (
    _EMOTION_OVERLAY,
    _GENERIC_FALLBACK,
    _NICHE_DEFAULTS,
)

_MOTION_CAMERA = re.compile(
    r"\b("
    r"push.?in|pull.?out|pull.?back|dolly|tracks?|tracking|orbit|orbits?"
    r"|pan(?:s|ning)?|tilt(?:s|ing)?|crane|booms?|booming|zooms?|zooming"
    r"|rotates?|rotating|rises?|rising|descends?|descending|drifts?|drifting"
    r"|static shot|locked.?off|handheld|micro.?shake"
    r")\b",
    re.IGNORECASE,
)

_CAMERA_VERB: dict[CameraMotion, str] = {
    CameraMotion.ken_burns: "camera slow push-in",
    CameraMotion.pan: "camera slow pan left",
    CameraMotion.zoom_out: "camera slow pull-back",
    CameraMotion.hold: "camera static drift",
    CameraMotion.parallax: "camera slow orbit",
}


def _atmosphere_tail(niche: str | None) -> str:
    base = _NICHE_DEFAULTS.get((niche or "").lower(), _GENERIC_FALLBACK)
    if "," in base:
        return base.split(",", 1)[1].strip()
    return base


def _subject_motion(emotion: EmotionType) -> str:
    overlay = _EMOTION_OVERLAY.get(emotion, "")
    if not overlay:
        return "subject shifts subtly, fabric ripples gently"
    if "," in overlay:
        return overlay.split(",", 1)[1].strip()
    return overlay


# Wan 2.2 I2V holds temporal coherence best with ONE dominant camera move plus a
# couple of small secondary motions. Stacking 5–6 motion clauses makes the model
# fight itself (warping, ghosting, jitter), so we cap the assembled prompt.
_MAX_MOTION_CLAUSES = 4


def _trim_clauses(prompt: str, limit: int = _MAX_MOTION_CLAUSES) -> str:
    """Keep at most ``limit`` comma-separated motion clauses, drop empties/dups."""
    seen: set[str] = set()
    kept: list[str] = []
    for part in prompt.split(","):
        p = part.strip()
        if not p:
            continue
        key = p.lower()
        if key in seen:
            continue
        seen.add(key)
        kept.append(p)
        if len(kept) >= limit:
            break
    return ", ".join(kept)


def _dedupe_leading_camera(camera: str, tail: str) -> str:
    """Drop a camera verb from ``tail`` so we don't issue two competing moves."""
    parts = [p.strip() for p in tail.split(",") if p.strip()]
    cleaned = [p for p in parts if not _MOTION_CAMERA.search(p)]
    body = ", ".join(cleaned or parts)
    return f"{camera}, {body}" if body else camera


def resolve_motion_prompt(
    clause: Clause,
    *,
    niche: str | None = None,
) -> str:
    """Return a Wan-ready motion prompt for one clause.

    The prompt always leads with exactly one camera move, then a subject action,
    then a little ambient motion, capped at ``_MAX_MOTION_CLAUSES`` clauses. A
    valid planner-authored ``motion_prompt`` is trusted but still trimmed so an
    over-eager LLM can't hand Wan a six-motion prompt that warps the clip.
    """
    explicit = (clause.motion_prompt or "").strip()
    if len(explicit) >= 20 and _MOTION_CAMERA.search(explicit):
        return _trim_clauses(explicit)

    camera = _CAMERA_VERB.get(clause.beat.camera, "camera slow push-in")
    subject = _subject_motion(clause.beat.emotion)
    atmosphere = _atmosphere_tail(niche)
    combined = _dedupe_leading_camera(camera, f"{subject}, {atmosphere}")
    return _trim_clauses(combined)
