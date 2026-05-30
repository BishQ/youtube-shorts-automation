"""FFmpeg upscale + sharpen helpers for I2V and still-image render paths."""

from __future__ import annotations

from shorts_pipeline.config.settings import Settings

_DEFAULT_SCALE_FLAGS = "spline+accurate_rnd+full_chroma_int"


def render_scale_flags(settings: Settings) -> str:
    raw = (getattr(settings, "render_scale_flags", None) or _DEFAULT_SCALE_FLAGS).strip()
    return raw or _DEFAULT_SCALE_FLAGS


def unsharp_filter_suffix(settings: Settings) -> str:
    if not getattr(settings, "render_unsharp_enabled", True):
        return ""
    size = int(getattr(settings, "render_unsharp_luma_size", 5))
    if size % 2 == 0:
        size += 1
    size = max(3, min(23, size))
    luma = float(getattr(settings, "render_unsharp_luma_amount", 0.45))
    chroma = float(getattr(settings, "render_unsharp_chroma_amount", 0.0))
    return (
        f",unsharp=luma_msize_x={size}:luma_msize_y={size}"
        f":luma_amount={luma:.4f}:chroma_msize_x=3:chroma_msize_y=3"
        f":chroma_amount={chroma:.4f}"
    )


def i2v_upscale_filter_chain(
    *,
    w: int,
    h: int,
    fps: int,
    duration_s: float,
    settings: Settings,
) -> str:
    """Scale low-res I2V MP4 (e.g. 480×832) to Shorts canvas with optional 2× pre-pass."""
    flags = render_scale_flags(settings)
    unsharp = unsharp_filter_suffix(settings)
    dur = max(0.05, float(duration_s))
    if getattr(settings, "render_i2v_two_step_upscale", True):
        return (
            f"scale=iw*2:ih*2:flags={flags},"
            f"scale={w}:{h}:force_original_aspect_ratio=increase:flags={flags},"
            f"crop={w}:{h}{unsharp},"
            f"fps={fps},trim=duration={dur:.6f},setpts=PTS-STARTPTS,format=yuv420p"
        )
    return (
        f"scale={w}:{h}:force_original_aspect_ratio=increase:flags={flags},"
        f"crop={w}:{h}{unsharp},"
        f"fps={fps},trim=duration={dur:.6f},setpts=PTS-STARTPTS,format=yuv420p"
    )


def still_image_scale_filter(*, work_w: int, work_h: int, settings: Settings) -> str:
    flags = render_scale_flags(settings)
    return (
        f"scale={work_w}:{work_h}:force_original_aspect_ratio=decrease:flags={flags},"
        f"pad={work_w}:{work_h}:(ow-iw)/2:(oh-ih)/2,setsar=1,"
        f"trim=end_frame=1,setpts=PTS-STARTPTS,format=yuv420p"
    )


def cover_scale_crop_filter(*, w: int, h: int, settings: Settings) -> str:
    flags = render_scale_flags(settings)
    return f"scale={w}:{h}:force_original_aspect_ratio=increase:flags={flags},crop={w}:{h}"
