"""Map beat emotion / camera / intensity to FFmpeg filter parameters.

The zoompan expressions here are designed for cinematic feel:
* Pushes use **ease-out** quadratic — fast at the start, decelerating into a
  hold, mimicking a real camera dolly settling on its subject.
* Pans use **smoothstep** (3p² − 2p³) — accelerates and decelerates instead
  of moving at a constant speed.
* Pull-outs (zoom_out) use **ease-out** as well so the reveal feels released
  rather than rushed.
* Holds breathe with a slow sine — the image is never perfectly static, which
  reads as "alive" footage instead of a slideshow.
* Hooks and shocks layer a tiny axis sway on top of the push so the camera
  feels handheld for those few seconds — much more kinetic than a perfectly
  rectilinear zoom.
* **Parallax** (single plate): ease-out zoom plus smoothstep lateral drift with
  a slightly out-of-phase vertical nudge so motion does not read as a plain Ken
  Burns push.
"""

from __future__ import annotations

from shorts_pipeline.planner.schema import CameraMotion, EmotionType, TransitionType


# ── Easing helper expressions ────────────────────────────────────────────────
# `n` is the total frame count of the zoompan run (already substituted in).
# We always express progress as `on/(n-1)` clamped to [0,1] so endpoints
# evaluate cleanly. All helpers return an FFmpeg expression string.

def _progress(n_frames: int) -> str:
    """0..1 progress through the clip. Clamped so we never overshoot zmax."""
    denom = max(1, n_frames - 1)
    return f"min(1,on/{denom})"


def _ease_out_quad(n_frames: int) -> str:
    """Decelerating ease (1 - (1-p)²). 0 at start, 1 at end, fast then slow."""
    p = _progress(n_frames)
    return f"(1-pow(1-{p},2))"


def _smoothstep(n_frames: int) -> str:
    """Smooth S-curve (3p² − 2p³). Slow in, fast through middle, slow out."""
    p = _progress(n_frames)
    return f"(3*pow({p},2)-2*pow({p},3))"


# ── Ken Burns / zoompan ───────────────────────────────────────────────────────

def zoompan_expr(  # noqa: C901
    camera: CameraMotion,
    n_frames: int,
    w: int,
    h: int,
    intensity: float = 0.5,
    clip_index: int = 0,
    emotion: EmotionType | None = None,
    fps: int = 30,
) -> str:
    """Return a complete `zoompan=...` filter chain string.

    Parameters
    ----------
    camera:
        Camera motion mode from the planner (ken_burns, pan, zoom_out, hold, parallax).
    n_frames:
        Number of output frames the zoompan should produce. Must equal
        `clip.duration_s * fps` — the trim before zoompan in the renderer is
        what makes this exact.
    w, h:
        Target canvas dimensions (final video size).
    intensity:
        0..1 from the beat. Higher → more zoom range, more aggressive sway.
    clip_index:
        Used to alternate horizontal anchor left / right between consecutive
        clips so the deck doesn't always pull toward the same corner.
    emotion:
        Optional emotion tag — chooses zmax, anchor, and easing flavour.
    """
    base = f"d={n_frames}:s={w}x{h}:fps={fps}"
    intensity = max(0.0, min(1.0, intensity))

    # Maximum zoom level — bigger emotions push further. We deliberately keep
    # zmax modest (1.55–1.85) so faces never blow up to soft pixels.
    if emotion == EmotionType.shock:
        zmax = 1.85
    elif emotion in (EmotionType.climactic, EmotionType.hook):
        zmax = 1.75
    elif emotion == EmotionType.triumphant:
        zmax = 1.65
    elif emotion in (EmotionType.tragic, EmotionType.reflective):
        zmax = 1.50
    else:
        zmax = 1.60

    zrange = zmax - 1.0  # how far to push from rest
    alt = clip_index % 2  # 0 = lean left, 1 = lean right

    # Anchor offsets per emotion. The `/zoom` divisor keeps the offset
    # constant in *output* pixels even as zoom changes.
    if emotion in (EmotionType.hook, EmotionType.shock):
        # Anchor low + slightly off-axis: feels grounded and urgent.
        x_off = w * 0.07 * (1 if alt else -1)
        y_off = -h * 0.05
        x_expr = f"iw/2-(iw/zoom/2)+{x_off:.0f}/zoom"
        y_expr = f"ih-ih/zoom/2+{y_off:.0f}/zoom"
    elif emotion == EmotionType.triumphant:
        # Anchor low → camera rises into the frame as it pushes in.
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = "ih-ih/zoom/2"
    elif emotion in (EmotionType.tragic, EmotionType.reflective):
        # Anchor high → contemplative top-down weight.
        x_expr = "iw/2-(iw/zoom/2)"
        y_off = h * 0.04
        y_expr = f"ih/zoom/2+{y_off:.0f}/zoom"
    elif emotion == EmotionType.climactic:
        # Diagonal swing — bigger lateral offset, off-centre vertical.
        x_off = w * 0.10 * (1 if alt else -1)
        y_off = h * 0.06 * (1 if alt else -1)
        x_expr = f"iw/2-(iw/zoom/2)+{x_off:.0f}/zoom"
        y_expr = f"ih/2-(ih/zoom/2)+{y_off:.0f}/zoom"
    elif emotion == EmotionType.tense_buildup:
        # Subtle off-centre creep.
        x_off = w * 0.06 * (1 if alt else -1)
        y_off = h * 0.03
        x_expr = f"iw/2-(iw/zoom/2)+{x_off:.0f}/zoom"
        y_expr = f"ih/2-(ih/zoom/2)+{y_off:.0f}/zoom"
    else:
        # Default: gentle horizontal lean.
        x_off = w * 0.05 * (1 if alt else -1)
        x_expr = f"iw/2-(iw/zoom/2)+{x_off:.0f}/zoom"
        y_expr = "ih/2-(ih/zoom/2)"

    # ── KEN BURNS push ──────────────────────────────────────────────────────
    if camera == CameraMotion.ken_burns:
        # Reflective / tragic: shorter zoom range, ease-in-out for a slow
        # contemplative drift. Everyone else gets ease-out: quick start,
        # smooth deceleration into a pro-feeling settled hold.
        if emotion in (EmotionType.tragic, EmotionType.reflective):
            curve = _smoothstep(n_frames)
            push = zrange * 0.55  # less aggressive
        elif emotion == EmotionType.shock:
            # Sharp punch-in: ease-out is even more pronounced for shocks.
            curve = _ease_out_quad(n_frames)
            push = zrange * 1.0
        else:
            curve = _ease_out_quad(n_frames)
            # Gentle clamp on push so calmer beats don't push the full zmax.
            push = zrange * (0.65 + 0.35 * intensity)
        return (
            f"zoompan=z='1+{push:.4f}*{curve}'"
            f":x='{x_expr}':y='{y_expr}':{base}"
        )

    # ── ZOOM OUT (reveal) ───────────────────────────────────────────────────
    if camera == CameraMotion.zoom_out:
        # Start at zmax, decelerate back to 1.0 — release / exhale feel.
        curve = _ease_out_quad(n_frames)
        return (
            f"zoompan=z='{zmax:.3f}-{zrange:.4f}*{curve}'"
            f":x='{x_expr}':y='{y_expr}':{base}"
        )

    # ── PAN ─────────────────────────────────────────────────────────────────
    if camera == CameraMotion.pan:
        # Smoothstep across the source image. Hold zoom so we have headroom
        # to actually move. Direction alternates per clip so consecutive pans
        # don't all glide the same way.
        zoom_const = 1.30
        curve = _smoothstep(n_frames)
        # Pan endpoints span 30 % of the source width; intensity scales travel.
        travel = 0.20 + 0.18 * intensity
        if alt:
            x_start = f"iw*({0.5 - travel/2:.4f})"
            x_end = f"iw*({0.5 + travel/2:.4f})"
        else:
            x_start = f"iw*({0.5 + travel/2:.4f})"
            x_end = f"iw*({0.5 - travel/2:.4f})"
        x_expr_pan = f"({x_start})+(({x_end})-({x_start}))*{curve}-iw/zoom/2"
        return (
            f"zoompan=z='{zoom_const}'"
            f":x='{x_expr_pan}':y='{y_expr}':{base}"
        )

    # ── PARALLAX (simulated depth on one still) ─────────────────────────────
    if camera == CameraMotion.parallax:
        # Zoom eases out while the crop window drifts on smoothstep — different
        # temporal curves mimic foreground vs background parallax on a 2D plate.
        curve_z = _ease_out_quad(n_frames)
        curve_x = _smoothstep(n_frames)
        sign = 1.0 if alt else -1.0
        push = zrange * (0.50 + 0.30 * intensity)
        travel_x = 0.07 + 0.14 * intensity
        y_tr = 0.02 + 0.04 * intensity
        lat = f"({sign}*iw*{travel_x:.4f}*{curve_x}/zoom)"
        y_drift = f"({-sign}*ih*{y_tr:.4f}*(1-({curve_z}))*{curve_x}/zoom)"
        x_p = f"iw/2-(iw/zoom/2)+{lat}"
        y_p = f"ih/2-(ih/zoom/2)+{y_drift}"
        return (
            f"zoompan=z='1+{push:.4f}*{curve_z}'"
            f":x='{x_p}':y='{y_p}':{base}"
        )

    # ── HOLD (breathing) ────────────────────────────────────────────────────
    # Constant slow sine on zoom — never perfectly static but never obvious.
    # Half a cycle over the whole clip = inhale across the shot.
    return (
        f"zoompan=z='1.04+0.030*sin(PI*on/{max(1, n_frames - 1)})'"
        f":x='{x_expr}':y='{y_expr}':{base}"
    )


# ── xfade parameters ──────────────────────────────────────────────────────────
# Slightly longer than before — modern pro Shorts use 0.30–0.50s blends.
# `dip_to_black` and `smash_white` keep their dramatic timing.

XFADE_TRANSITION: dict[TransitionType, str] = {
    TransitionType.xfade:          "fade",
    TransitionType.dip_to_black:   "fadeblack",
    TransitionType.smash_white:    "fadewhite",
    TransitionType.hard_cut:       "fade",        # duration=0 → hard cut
    TransitionType.paint_splatter: "dissolve",    # organic random-pixel dissolve
    TransitionType.vr_light_rays:  "fadewhite",   # burst through white → light rays
    TransitionType.random_blocks:  "pixelize",    # block pixelization wipe
    TransitionType.center_split:   "horzopen",    # curtain split from center
}

XFADE_DURATION: dict[TransitionType, float] = {
    TransitionType.xfade:          0.35,
    TransitionType.dip_to_black:   0.50,
    TransitionType.smash_white:    0.12,
    TransitionType.hard_cut:       0.0,
    TransitionType.paint_splatter: 0.40,
    TransitionType.vr_light_rays:  0.25,
    TransitionType.random_blocks:  0.35,
    TransitionType.center_split:   0.40,
}
