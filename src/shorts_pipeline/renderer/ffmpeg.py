"""
FFmpeg render engine — implements all 13 Golden Rules.

Rule  1: Camera motion on every clip (zoompan: Ken Burns, pan, zoom-out, parallax, hold)
Rule  2: Pacing curve encoded in ClipSpec.duration_s (handled by editor/pacing.py)
Rule  3: Cuts on first word of clause (cut_at_s from word aligner)
Rule  4: BGM sidechaincompress duck during narration
Rule  5: First frame is hard-cut (no fade-in from black, full brightness at t=0).
         Subsequent clips may xfade — only the opening uses hard_cut.
Rule  6: Last frame breathes 0.8 s post-narration; fade to black 0.6 s
Rule  7: Emphasis words → scale+glow in ASS (ass.py) + 1-frame light-leak overlay
Rule  8: One LUT per video (lut3d filter), chosen by planner
Rule  9: Clip transitions — FFmpeg xfade (fixed beat map or full random catalog)
Rule 10: Whoosh SFX at every non-hard-cut transition (−18 dB)
Rule 11: Vignette + film grain always on
Rule 12: Subtitle position set per-clause by face detector (ass.py + face_locator.py)
Rule 13: End plate — black bg + loop-hook question
"""

from __future__ import annotations

import math
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path

from shorts_pipeline.config.settings import Settings
from shorts_pipeline.editor.pacing import MAX_CLIP_DURATION_S
from shorts_pipeline.editor.emotion_to_filtergraph import zoompan_expr
from shorts_pipeline.editor.models import ClipSpec, EditPlan
from shorts_pipeline.editor.xfade_effects import resolve_xfade_for_clip
from shorts_pipeline.logging_setup import get_logger
from shorts_pipeline.planner.schema import TransitionType
from shorts_pipeline.renderer.quality import RenderQualityError, validate_ass_for_render, validate_render_output

log = get_logger(__name__)

# Duration of the light-leak overlay on emphasis words (Rule 7)
_LIGHT_LEAK_DURATION_S = 1.0 / 30.0  # 1 frame at 30 fps


def transition_chain_duration_s(clips: list[ClipSpec]) -> float:
    """Duration (seconds) of the video after `_build_transition_chain`.

    Xfade transitions overlap adjacent clips, so this is **shorter** than
    ``sum(c.duration_s)``. When it is shorter than ``narration_duration_s``,
    the mux would freeze the last frame while audio (and ASS) continue — pad
    before post-processing so snow/subtitles stay in sync for the full narr.
    """
    if not clips:
        return 0.0
    n = len(clips)
    if n == 1:
        return max(0.0, clips[0].duration_s)
    cumulative_s = clips[0].duration_s
    for i in range(1, n):
        clip = clips[i]
        trans = clip.transition_in
        _xf_name, xd = resolve_xfade_for_clip(clip)
        if trans == TransitionType.hard_cut or xd < 0.001:
            cumulative_s += clip.duration_s
        else:
            xd = min(xd, cumulative_s * 0.9, clip.duration_s * 0.9)
            offset = max(0.0, cumulative_s - xd)
            cumulative_s = offset + clip.duration_s
    return cumulative_s


def compensate_xfade_overlap(
    clips: list[ClipSpec],
    narration_duration_s: float,
    *,
    max_clip_duration_s: float = MAX_CLIP_DURATION_S,
) -> list[ClipSpec]:
    """Spread xfade overlap compensation across clips instead of padding the last frame.

    Pacing caps clause/image durations at 4.5s, but xfade transitions overlap
    adjacent clips and shorten the final chain. If we simply tpad the difference,
    the last image freezes for several seconds. This raises shorter clips toward
    the max duration until the transition chain covers the narration timeline.
    """
    if not clips:
        return clips

    chain_dur = transition_chain_duration_s(clips)
    missing_s = float(narration_duration_s) - chain_dur
    if missing_s <= 0.001:
        return clips

    out = list(clips)
    # Prefer later clips first because they are commonly shorter after the 4.5s
    # clamp pass, but never exceed the per-image max.
    for idx in range(len(out) - 1, -1, -1):
        if missing_s <= 0.001:
            break
        clip = out[idx]
        room = max(0.0, max_clip_duration_s - clip.duration_s)
        if room <= 0.001:
            continue
        add_s = min(room, missing_s)
        out[idx] = replace(clip, duration_s=clip.duration_s + add_s)
        missing_s -= add_s

    return out


class RenderError(Exception):
    pass


@dataclass
class RenderRequest:
    edit: EditPlan
    narration_wav: Path
    ass_path: Path
    out_mp4: Path
    narration_duration_s: float
    outro_image_path: Path | None = None


def _escape_path_for_filter(p: Path) -> str:
    s = str(p.resolve()).replace("\\", "/").replace(":", r"\:")
    return s.replace("'", r"\'")


def _escape_drawtext(s: str) -> str:
    """Escape text for FFmpeg drawtext filter."""
    return (
        s.replace("\\", "\\\\")
        .replace("'", "’")   # replace straight quote with curly (safe)
        .replace(":", r"\:")
        .replace("%", r"\%")
    )


# ── Filter-complex helpers ────────────────────────────────────────────────────

def _add_visual_clips(
    parts: list[str],
    clips: list[ClipSpec],
    settings: Settings,
    edit: EditPlan,
    w: int,
    h: int,
    fps: int,
    *,
    breathe_s: float,
    n: int,
) -> None:
    """Prepare each clip input as [vkb{i}] — Wan MP4 trim/scale or Ken Burns PNG."""
    work_w = int(w * 1.8)
    work_h = int(h * 1.8)
    fallback_lut_path = settings.resolve_lut(edit.lut_choice.value)

    for clip in clips:
        i = clip.index
        dur = clip.duration_s + (breathe_s if i == n - 1 else 0.0)
        use_video = clip.video_path is not None and clip.video_path.is_file()

        if use_video:
            raw_tag = f"vkb{i}_raw"
            parts.append(
                f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,"
                f"crop={w}:{h},fps={fps},trim=duration={dur:.6f},setpts=PTS-STARTPTS,"
                f"format=yuv420p[{raw_tag}]"
            )
        else:
            frames = max(1, math.ceil(dur * fps))
            zp = zoompan_expr(
                camera=clip.camera,
                n_frames=frames,
                w=w,
                h=h,
                intensity=clip.intensity,
                clip_index=i,
                emotion=clip.emotion,
            )
            parts.append(
                f"[{i}:v]scale={work_w}:{work_h}:force_original_aspect_ratio=decrease:flags=lanczos,"
                f"pad={work_w}:{work_h}:(ow-iw)/2:(oh-ih)/2,setsar=1,"
                f"trim=end_frame=1,setpts=PTS-STARTPTS,format=yuv420p[sc{i}]"
            )
            raw_tag = f"vkb{i}_raw"
            parts.append(
                f"[sc{i}]{zp},fps={fps},settb=1/{fps},"
                f"trim=duration={dur:.6f},format=yuv420p,setpts=PTS-STARTPTS[{raw_tag}]"
            )

        lut_path = (
            settings.resolve_lut(clip.color_grade.value)
            if clip.color_grade is not None
            else fallback_lut_path
        )
        if lut_path is not None:
            lut_esc = _escape_path_for_filter(lut_path)
            parts.append(f"[{raw_tag}]lut3d=file='{lut_esc}'[vkb{i}]")
        else:
            parts.append(f"[{raw_tag}]null[vkb{i}]")


def _build_transition_chain(
    parts: list[str],
    clips: list[ClipSpec],
    fps: int,
) -> str:
    """
    Chain clips with xfade / concat.  Returns the output stream tag.

    For hard_cut transitions: concat (no blend).
    For all others: xfade with the appropriate transition type and duration.
    """
    n = len(clips)
    if n == 0:
        raise RenderError("no clips to chain")
    if n == 1:
        return "vkb0"

    current_tag = "vkb0"
    # cumulative_s is the output duration of the current chain so far.
    # This is used to compute xfade offsets.
    cumulative_s = clips[0].duration_s

    for i in range(1, n):
        clip = clips[i]
        trans = clip.transition_in
        _xf_name, xd = resolve_xfade_for_clip(clip)
        out_tag = f"vtrans{i}"

        raw_tag = f"{out_tag}_raw"
        if trans == TransitionType.hard_cut or xd < 0.001:
            parts.append(
                f"[{current_tag}][vkb{i}]concat=n=2:v=1:a=0[{raw_tag}]"
            )
            cumulative_s += clip.duration_s
        else:
            # Clamp xd so it never exceeds the shorter of the two clips
            xd = min(xd, cumulative_s * 0.9, clip.duration_s * 0.9)
            offset = max(0.0, cumulative_s - xd)
            xfade_name, _ = resolve_xfade_for_clip(clip)
            parts.append(
                f"[{current_tag}][vkb{i}]xfade=transition={xfade_name}"
                f":duration={xd:.6f}:offset={offset:.6f}[{raw_tag}]"
            )
            cumulative_s = offset + clip.duration_s
        # Re-normalise timebase so subsequent xfade sees a consistent 1/fps tb
        parts.append(
            f"[{raw_tag}]fps={fps},settb=1/{fps},setpts=PTS-STARTPTS[{out_tag}]"
        )

        current_tag = out_tag

    return current_tag


def _add_post_processing(
    parts: list[str],
    video_in: str,
    settings: Settings,
    edit: EditPlan,
    narration_s: float,
    w: int,
    h: int,
    fps: int,
    idx_ep_bg: int | None = None,
    is_outro_image: bool = False,
) -> str:
    """Apply vignette → grain → fade-out → end-plate. Returns final video tag.

    Per-clip LUT colour grading is applied earlier inside `_add_visual_clips`
    (one LUT per beat instead of one LUT for the whole video).
    """
    v = video_in

    # Pad so base video length matches narration timeline (xfade overlaps and
    # lead-in vs sum(durations) can otherwise end the picture stream early).
    chain_dur = transition_chain_duration_s(edit.clips)
    pad_main = max(0.0, float(narration_s) - chain_dur)
    if pad_main > 0.001:
        parts.append(
            f"[{v}]tpad=stop_mode=clone:stop_duration={pad_main:.6f}[vpadnarr]"
        )
        v = "vpadnarr"

    # ── Rule 11a: Vignette ───────────────────────────────────────────────────
    if settings.vignette_angle > 0:
        parts.append(f"[{v}]vignette=angle={settings.vignette_angle:.4f}[vvig]")
        v = "vvig"

    # ── Rule 11b: Film grain (noise) ─────────────────────────────────────────
    if settings.grain_strength > 0:
        parts.append(f"[{v}]noise=alls={settings.grain_strength}:allf=t+u[vgrain]")
        v = "vgrain"

    # ── Rule 6: Last-frame breathe + fade to black before end plate ──────────
    # The transition chain only runs through narration_s seconds — the
    # breathe period must be added explicitly here by cloning the last frame.
    breathe_s = settings.last_frame_breathe_s
    fade_s = settings.fade_out_duration_s
    if breathe_s > 0:
        parts.append(
            f"[{v}]tpad=stop_duration={breathe_s:.6f}:stop_mode=clone[vbreathe]"
        )
        v = "vbreathe"
    fade_start = max(0.0, narration_s + breathe_s - fade_s)
    parts.append(
        f"[{v}]fade=t=out:st={fade_start:.6f}:d={fade_s:.6f}[vmain_fade]"
    )
    v = "vmain_fade"

    # ── Rule 13: Pro end plate ───────────────────────────────────────────────
    # Background  : last image, scaled to cover, blurred + darkened
    # Question    : center, fade-in over 0.4 s + slide-up 60 px
    # CTA         : lower third, appears at 0.7 s with fade-in (yellow accent)
    ep_dur = settings.end_plate_duration_s
    if settings.end_plate_enabled and ep_dur > 0 and idx_ep_bg is not None:
        v = _add_end_plate(parts, v, settings, edit, ep_dur, w, h, fps, idx_ep_bg, is_outro_image=is_outro_image)

    return v


def _add_end_plate(
    parts: list[str],
    main_video_tag: str,
    settings: Settings,
    edit: EditPlan,
    ep_dur: float,
    w: int,
    h: int,
    fps: int,
    idx_ep_bg: int,
    *,
    is_outro_image: bool = False,
) -> str:
    """Build the cinematic end-plate segment and concat it to the main video.

    When is_outro_image=True the background is a dedicated Flux-generated image
    (lighter blur, less darken) so the artwork stays visible behind the text.

    Layout (top → bottom):
      • Hook question  — slide-up + fade-in, appears first
      • THANKS FOR WATCHING — fade-in at 0.55 s
      • ▶  SUBSCRIBE FOR MORE — yellow, fade-in at 0.90 s

    Returns the new output stream tag.
    """
    q_text = _escape_drawtext(edit.end_plate_question)
    thanks_text = _escape_drawtext(settings.end_plate_thanks_text)
    cta_text = _escape_drawtext(settings.end_plate_cta_text)
    q_fs = settings.end_plate_font_size
    thanks_fs = settings.end_plate_thanks_font_size
    cta_fs = settings.end_plate_cta_font_size
    font_name = settings.ass_font_name
    cta_colour = settings.end_plate_cta_colour

    # Animation timing (seconds, relative to start of end plate)
    q_slide_s = 0.45       # question slide+fade duration
    q_slide_px = 60        # slide travel distance (px)
    thanks_appear_s = 0.55 # when "THANKS FOR WATCHING" starts fading in
    thanks_fade_s = 0.30
    cta_appear_s = 0.90    # when CTA starts fading in
    cta_fade_s = 0.30

    # Background: dedicated outro image → configurable blur (low/zero keeps
    # pre-designed art crisp) + mild darken so overlay text stays readable.
    # Generic last-clip fallback → heavy blur + heavy darken (original behaviour).
    if is_outro_image:
        blur_sigma = settings.outro_image_blur_sigma
        brightness = -0.12 if blur_sigma == 0 else -0.22
        saturation = 0.95 if blur_sigma == 0 else 0.92
    else:
        blur_sigma = 24
        brightness = -0.40
        saturation = 0.85

    blur_filter = f"gblur=sigma={blur_sigma}," if blur_sigma > 0 else ""
    parts.append(
        f"[{idx_ep_bg}:v]"
        f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={w}:{h},setsar=1,fps={fps},settb=1/{fps},"
        f"trim=duration={ep_dur:.6f},setpts=PTS-STARTPTS,"
        f"{blur_filter}eq=brightness={brightness}:saturation={saturation},"
        f"format=yuv420p[ep_bg]"
    )

    # Hook question: slide-up + fade-in, upper-center of frame.
    q_y_expr = (
        f"h*0.28-text_h/2 + "
        f"if(lt(t,{q_slide_s}),(1-t/{q_slide_s})*{q_slide_px},0)"
    )
    q_alpha_expr = f"if(lt(t,{q_slide_s}),t/{q_slide_s},1)"
    parts.append(
        f"[ep_bg]drawtext=text='{q_text}'"
        f":font={font_name}:fontsize={q_fs}:fontcolor=white"
        f":x=(w-text_w)/2:y='{q_y_expr}'"
        f":alpha='{q_alpha_expr}'"
        f":borderw=4:bordercolor=black@0.85"
        f":shadowcolor=black@0.6:shadowx=0:shadowy=4[ep_q]"
    )

    # "THANKS FOR WATCHING": fade-in, centered vertically.
    thanks_alpha_expr = (
        f"if(lt(t,{thanks_appear_s}),0,"
        f"min(1,(t-{thanks_appear_s})/{thanks_fade_s}))"
    )
    parts.append(
        f"[ep_q]drawtext=text='{thanks_text}'"
        f":font={font_name}:fontsize={thanks_fs}:fontcolor=white"
        f":x=(w-text_w)/2:y=h*0.54-text_h/2"
        f":alpha='{thanks_alpha_expr}'"
        f":borderw=3:bordercolor=black@0.85"
        f":shadowcolor=black@0.55:shadowx=0:shadowy=3[ep_thanks]"
    )

    # CTA "▶  SUBSCRIBE FOR MORE": yellow, fade-in last, lower third.
    cta_alpha_expr = (
        f"if(lt(t,{cta_appear_s}),0,"
        f"min(1,(t-{cta_appear_s})/{cta_fade_s}))"
    )
    parts.append(
        f"[ep_thanks]drawtext=text='{cta_text}'"
        f":font={font_name}:fontsize={cta_fs}:fontcolor={cta_colour}"
        f":x=(w-text_w)/2:y=h*0.72"
        f":alpha='{cta_alpha_expr}'"
        f":borderw=3:bordercolor=black@0.9"
        f":shadowcolor=black@0.7:shadowx=0:shadowy=3[ep]"
    )

    parts.append(f"[{main_video_tag}][ep]concat=n=2:v=1:a=0[vwep]")
    return "vwep"


def _add_audio(
    parts: list[str],
    idx_narr: int,
    idx_bgm: int,
    idx_whoosh: int | None,
    clips: list[ClipSpec],
    settings: Settings,
    narration_s: float,
    ep_dur: float,
    sfx_clip_tags: dict[int, str] | None = None,
) -> None:
    """Build audio chain → [aout].

    Rule  4: BGM sidechaincompress (ducks during narration)
    Rule 10: Whoosh SFX at non-hard-cut transitions at -18 dB
    """
    breathe_s = settings.last_frame_breathe_s
    tail_pad = float(settings.narration_tail_silence_pad_s or 0.0)
    total_dur = narration_s + breathe_s + ep_dur

    # Narration — pad to cover breathing room + end plate, then split into two:
    # [narr_mix]  → goes into the final amix as the audible narration track
    # [narr_sc]   → used only as the sidechain signal for sidechaincompress
    # (FFmpeg filter_complex labels can only be consumed once)
    parts.append(
        f"[{idx_narr}:a]asetpts=PTS-STARTPTS,aresample=async=1,"
        f"apad=pad_dur={breathe_s + ep_dur + tail_pad:.6f},"
        f"asplit=2[narr_mix][narr_sc]"
    )

    # BGM — loop if needed, trim to exact video duration, fade-in 1s + fade-out 1.5s.
    # The fade-out kicks in during the end-plate so the music ends cleanly.
    bgm_fade_out_d = 1.5
    bgm_fade_out_st = max(0.0, total_dur - bgm_fade_out_d)
    parts.append(
        f"[{idx_bgm}:a]asetpts=PTS-STARTPTS,aresample=async=1,"
        f"aloop=loop=-1:size=2000000000,atrim=0:{total_dur:.6f},"
        f"afade=t=in:st=0:d=1.0,"
        f"afade=t=out:st={bgm_fade_out_st:.6f}:d={bgm_fade_out_d:.6f}[bgm_raw]"
    )

    # Rule 4: sidechaincompress — BGM ducked by narration sidechain
    sc_thresh = settings.bgm_sidechain_threshold
    sc_ratio = settings.bgm_sidechain_ratio
    bgm_vol = settings.bgm_volume_narration
    parts.append(
        f"[bgm_raw][narr_sc]sidechaincompress="
        f"threshold={sc_thresh}:ratio={sc_ratio:.1f}:attack=20:release=200"
        f":level_sc=1[bgm_compressed]"
    )
    if settings.bgm_volume_narration_db is not None:
        bgm_vol_arg = f"{settings.bgm_volume_narration_db:.3f}dB"
    else:
        bgm_vol_arg = f"{bgm_vol:.4f}"
    parts.append(f"[bgm_compressed]volume={bgm_vol_arg}[bgm_ducked]")

    # Rule 10: Whoosh SFX at non-hard-cut transitions
    whoosh_tags: list[str] = []
    if idx_whoosh is not None:
        for clip in clips[1:]:   # skip clip 0 (no incoming transition)
            if clip.transition_in != TransitionType.hard_cut:
                delay_ms = max(0, int(clip.cut_at_s * 1000))
                tag = f"whoosh_{clip.index}"
                parts.append(
                    f"[{idx_whoosh}:a]adelay={delay_ms}|{delay_ms},"
                    f"volume=-18dB[{tag}]"
                )
                whoosh_tags.append(f"[{tag}]")

    # Also add audio events (low_rumble, impact) from ClipSpec
    audio_event_tags = _add_audio_events(parts, clips, sfx_clip_tags or {})

    all_streams = ["[narr_mix]", "[bgm_ducked]"] + whoosh_tags + audio_event_tags
    n_streams = len(all_streams)
    if n_streams == 1:
        parts.append("[narr_mix]anull[aout]")
    else:
        parts.append(
            f"{''.join(all_streams)}amix=inputs={n_streams}"
            f":duration=longest:dropout_transition=0[aout]"
        )


def _add_audio_events(
    parts: list[str],
    clips: list[ClipSpec],
    sfx_clip_tags: dict[int, str],
) -> list[str]:
    """Add audio_event SFX delay chains and return their output stream tags.

    sfx_clip_tags maps clip.index → unique pre-split source tag for that clip.
    Each source tag must appear exactly once as an input (caller handles asplit).
    """
    tags: list[str] = []
    for clip in clips:
        src_tag = sfx_clip_tags.get(clip.index)
        if src_tag is None:
            continue
        delay_ms = max(0, int(clip.cut_at_s * 1000))
        out_tag = f"sfxev_{clip.index}"
        parts.append(
            f"[{src_tag}]adelay={delay_ms}|{delay_ms},volume=-12dB[{out_tag}]"
        )
        tags.append(f"[{out_tag}]")
    return tags


# ── Public API ────────────────────────────────────────────────────────────────

def build_ffmpeg_argv(req: RenderRequest, settings: Settings) -> list[str]:  # noqa: C901
    edit = req.edit
    clips = compensate_xfade_overlap(edit.clips, req.narration_duration_s)
    edit = replace(edit, clips=clips)
    n = len(clips)

    if n == 0:
        raise RenderError("EditPlan has no clips")
    if not req.ass_path.is_file():
        raise RenderError(f"missing ASS subtitles: {req.ass_path}")
    if not req.narration_wav.is_file():
        raise RenderError(f"missing narration wav: {req.narration_wav}")
    if not edit.bgm_path.is_file():
        raise RenderError(f"missing BGM: {edit.bgm_path}")
    try:
        validate_ass_for_render(req.ass_path)
    except RenderQualityError as e:
        raise RenderError(str(e)) from e

    w = settings.video_width
    h = settings.video_height
    fps = settings.video_fps

    argv: list[str] = [settings.ffmpeg_path, "-y"]

    # ── Inputs: clause visuals (Wan MP4 or looped PNG) ───────────────────────
    breathe_s = settings.last_frame_breathe_s
    for i, clip in enumerate(clips):
        img_dur = clip.duration_s + (breathe_s if i == n - 1 else 0.0)
        if clip.video_path is not None and clip.video_path.is_file():
            argv.extend(["-i", str(clip.video_path.resolve())])
        else:
            argv.extend(["-loop", "1", "-t", f"{img_dur:.6f}", "-i", str(clip.image_path.resolve())])

    # ── Input: narration ─────────────────────────────────────────────────────
    argv.extend(["-i", str(req.narration_wav.resolve())])
    idx_narr = n

    # ── Input: BGM ───────────────────────────────────────────────────────────
    argv.extend(["-i", str(edit.bgm_path.resolve())])
    idx_bgm = n + 1
    next_idx = n + 2

    # ── Input: whoosh SFX ────────────────────────────────────────────────────
    idx_whoosh: int | None = None
    has_non_hard_cut = any(
        c.transition_in != TransitionType.hard_cut for c in clips[1:]
    )
    whoosh_path = settings.resolve_sfx("whoosh")
    if has_non_hard_cut and whoosh_path is not None:
        argv.extend(["-i", str(whoosh_path.resolve())])
        idx_whoosh = next_idx
        next_idx += 1

    # ── Inputs: audio event SFX ──────────────────────────────────────────────
    # Collect unique SFX files needed for audio_events in clips
    from shorts_pipeline.planner.schema import AudioEvent
    sfx_event_map: dict[str, int] = {}  # sfx_name → input index
    sfx_name_map = {
        AudioEvent.low_rumble: "low_rumble",
        AudioEvent.impact: "impact",
        AudioEvent.paper_flutter: "paper_flutter",
        AudioEvent.crowd_cheer: "crowd_cheer",
        AudioEvent.sword_clash: "sword_clash",
        AudioEvent.horse_gallop: "horse_gallop",
        AudioEvent.fire_crackle: "fire_crackle",
        AudioEvent.thunder_crack: "thunder_crack",
        AudioEvent.crowd_murmur: "crowd_murmur",
    }
    for clip in clips:
        sfx_name = sfx_name_map.get(clip.audio_event)
        if sfx_name and sfx_name not in sfx_event_map:
            p = settings.resolve_sfx(sfx_name)
            if p is not None:
                argv.extend(["-i", str(p.resolve())])
                sfx_event_map[sfx_name] = next_idx
                next_idx += 1

    # ── Input: atmospheric overlay (snow / dust / particles) ─────────────────
    idx_overlay: int | None = None
    overlay_path = settings.resolve_overlay()
    if overlay_path is not None:
        # -stream_loop -1 makes the overlay clip repeat for the whole narration
        argv.extend([
            "-stream_loop", "-1",
            "-i", str(overlay_path.resolve()),
        ])
        idx_overlay = next_idx
        next_idx += 1

    # ── Input: end-plate background ───────────────────────────────────────────
    # Use the dedicated Flux outro image when available; fall back to last clip.
    idx_ep_bg: int | None = None
    is_outro_image = False
    ep_dur_for_input = (
        settings.end_plate_duration_s if settings.end_plate_enabled else 0.0
    )
    if ep_dur_for_input > 0 and clips:
        if req.outro_image_path is not None and req.outro_image_path.is_file():
            ep_bg_img = req.outro_image_path.resolve()
            is_outro_image = True
        else:
            ep_bg_img = clips[-1].image_path.resolve()
        argv.extend([
            "-loop", "1",
            "-t", f"{ep_dur_for_input:.6f}",
            "-i", str(ep_bg_img),
        ])
        idx_ep_bg = next_idx
        next_idx += 1

    # ── Input: watermark (disabled — not applied to any render) ──────────────
    idx_wm: int | None = None

    # ── Filter complex ────────────────────────────────────────────────────────
    parts: list[str] = []

    # Build per-clip SFX source tags using asplit for sources used >1 time.
    # FFmpeg filter_complex labels can only appear once as an input, so any
    # SFX file used by multiple clips must be split into N independent copies.
    sfx_name_map2 = {
        AudioEvent.low_rumble: "low_rumble",
        AudioEvent.impact: "impact",
        AudioEvent.paper_flutter: "paper_flutter",
        AudioEvent.crowd_cheer: "crowd_cheer",
        AudioEvent.sword_clash: "sword_clash",
        AudioEvent.horse_gallop: "horse_gallop",
        AudioEvent.fire_crackle: "fire_crackle",
        AudioEvent.thunder_crack: "thunder_crack",
        AudioEvent.crowd_murmur: "crowd_murmur",
    }
    # Group clip indices by SFX name
    sfx_usage: dict[str, list[int]] = {}
    for clip in clips:
        sfx_name = sfx_name_map2.get(clip.audio_event)
        if sfx_name and sfx_name in sfx_event_map:
            sfx_usage.setdefault(sfx_name, []).append(clip.index)

    sfx_clip_tags: dict[int, str] = {}  # clip.index → unique source tag
    for sfx_name, clip_indices in sfx_usage.items():
        sfx_idx = sfx_event_map[sfx_name]
        if len(clip_indices) == 1:
            tag = f"sfx_{sfx_name}_i{clip_indices[0]}"
            parts.append(f"[{sfx_idx}:a]anull[{tag}]")
            sfx_clip_tags[clip_indices[0]] = tag
        else:
            split_tags = [f"sfx_{sfx_name}_i{ci}" for ci in clip_indices]
            outputs = "".join(f"[{t}]" for t in split_tags)
            parts.append(f"[{sfx_idx}:a]asplit={len(clip_indices)}{outputs}")
            for ci, tag in zip(clip_indices, split_tags):
                sfx_clip_tags[ci] = tag

    # Rule 1 + Rule 8: Wan MP4 or emotion-aware Ken Burns + per-clip LUT
    _add_visual_clips(parts, clips, settings, edit, w, h, fps, breathe_s=breathe_s, n=n)

    # Rule 9: Chain transitions
    video_out = _build_transition_chain(parts, clips, fps)

    # Rules 6, 11, 13 (LUT handled per-clip in _add_visual_clips above)
    video_out = _add_post_processing(
        parts,
        video_out,
        settings,
        edit,
        req.narration_duration_s,
        w,
        h,
        fps,
        idx_ep_bg=idx_ep_bg,
        is_outro_image=is_outro_image,
    )

    # Atmospheric overlay (snow / dust / particles) — screen-blend so the dark
    # background of the overlay video drops out and only bright particles stay.
    # Placed BEFORE subtitles so the text always sits on top of the snow.
    #
    # Two important details here:
    #   1. The `screen` blend MUST run in RGB (gbrp). Blending raw YUV chroma
    #      channels averages U and V around 128 incorrectly and tints the whole
    #      frame purple/magenta. Convert both inputs to gbrp, blend, then go
    #      back to yuv420p.
    #   2. `shortest=1` makes the blend stop as soon as the base video ends.
    #      Without it, the `-stream_loop -1` overlay keeps producing frames
    #      forever and ffmpeg encodes minutes of pure overlay after the video
    #      content has finished (this is what produced the ~8 minute output).
    if idx_overlay is not None:
        # Scale overlay to 420% of its native size (zoom-in to enlarge particles),
        # center-crop to the video canvas, then subtract-blend at full opacity.
        # Subtract mode: dark overlay background drops out; bright snow particles
        # subtract from the base frame for a stylized layered look.
        # shortest=1 stops the blend when the base video ends (stream_loop keeps
        # the overlay looping indefinitely otherwise).
        parts.append(
            f"[{idx_overlay}:v]scale=iw*4.2:ih*4.2:flags=bicubic,"
            f"crop={w}:{h}:(iw-{w})/2:(ih-{h})/2,"
            f"setsar=1,fps={fps},settb=1/{fps},format=gbrp,"
            f"setpts=PTS-STARTPTS[ovr_rgb]"
        )
        parts.append(f"[{video_out}]format=gbrp[vbase_rgb]")
        parts.append(
            "[vbase_rgb][ovr_rgb]blend=all_mode=subtract:all_opacity=1.0"
            ":shortest=1[vovr_rgb]"
        )
        parts.append("[vovr_rgb]format=yuv420p[vovr]")
        video_out = "vovr"

    # Rule 12: ASS subtitles (position set per-clause in ass.py)
    ass_esc = _escape_path_for_filter(req.ass_path)
    parts.append(f"[{video_out}]ass=filename='{ass_esc}'[vsub]")
    video_out = "vsub"

    # Watermark overlay
    if idx_wm is not None:
        alpha = max(0.0, min(1.0, settings.watermark_opacity))
        parts.append(
            f"[{idx_wm}:v]scale=-1:{int(h * 0.055)},format=rgba,"
            f"colorchannelmixer=aa={alpha}[wmv]"
        )
        parts.append(f"[{video_out}][wmv]overlay=W-w-28:H-h-28[vfinal]")
        video_out = "vfinal"

    # Rules 4, 10: Audio pipeline
    ep_dur = settings.end_plate_duration_s if settings.end_plate_enabled else 0.0
    _add_audio(
        parts,
        idx_narr=idx_narr,
        idx_bgm=idx_bgm,
        idx_whoosh=idx_whoosh,
        clips=clips,
        settings=settings,
        narration_s=req.narration_duration_s,
        ep_dur=ep_dur,
        sfx_clip_tags=sfx_clip_tags,
    )

    # Hard cap on output duration: narration + last-frame breathe + end plate,
    # clamped to render_max_shorts_duration_s so a long narration + tail pad
    # never pushes the output past the platform limit (e.g. 59.3 s for Shorts).
    total_dur = min(
        req.narration_duration_s + settings.last_frame_breathe_s + ep_dur,
        settings.render_max_shorts_duration_s,
    )

    argv.extend(["-filter_complex", ";".join(parts)])
    argv.extend(["-map", f"[{video_out}]", "-map", "[aout]"])
    argv.extend([
        "-t", f"{total_dur:.6f}",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-movflags", "+faststart",
        str(req.out_mp4.resolve()),
    ])
    return argv


def render_short(req: RenderRequest, settings: Settings) -> Path:
    argv = build_ffmpeg_argv(req, settings)
    log.info("ffmpeg_invocation", argc=len(argv))
    total_dur = sum(c.duration_s for c in req.edit.clips)
    # Heavy filter chain (zoompan, LUT, vignette, grain, shake, ASS, audio mix) on
    # CPU-only encoding can run 60–80× realtime. Give it generous headroom.
    timeout = max(1800, int(total_dur * 80) + 300)
    try:
        proc = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        raise RenderError("ffmpeg timeout") from e
    if proc.returncode != 0:
        log.error("ffmpeg_failed", stderr=proc.stderr[-4000:])
        raise RenderError(f"ffmpeg exited {proc.returncode}: {proc.stderr[-800:]}")
    if not req.out_mp4.is_file() or req.out_mp4.stat().st_size < 1024:
        raise RenderError(f"ffmpeg produced invalid output: {req.out_mp4}")
    ep_dur = settings.end_plate_duration_s if settings.end_plate_enabled else 0.0
    expected_duration_s = req.narration_duration_s + settings.last_frame_breathe_s + ep_dur
    try:
        probe = validate_render_output(
            mp4_path=req.out_mp4,
            ass_path=req.ass_path,
            expected_duration_s=expected_duration_s,
            settings=settings,
        )
    except RenderQualityError as e:
        raise RenderError(str(e)) from e
    log.info(
        "render_quality_pass",
        duration_s=round(probe.duration_s, 3),
        size_bytes=probe.size_bytes,
        width=probe.width,
        height=probe.height,
        fps=round(probe.fps, 3),
    )
    return req.out_mp4
