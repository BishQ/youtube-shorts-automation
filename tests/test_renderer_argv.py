import wave
from pathlib import Path

from shorts_pipeline.config.settings import Settings
from shorts_pipeline.editor.models import ClipSpec, EditPlan
from shorts_pipeline.planner.schema import (
    AudioEvent,
    CameraMotion,
    LutChoice,
    SubtitlePosition,
    TransitionType,
)
from shorts_pipeline.renderer.ffmpeg import (
    RenderRequest,
    build_ffmpeg_argv,
    compensate_xfade_overlap,
    transition_chain_duration_s,
)


def _write_min_png(path: Path) -> None:
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
        b"\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xdb\x00\x00\x00\x00IEND\xaeB`\x82"
    )


def _write_min_wav(path: Path, seconds: float = 1.0) -> None:
    fr = 8000
    n = int(fr * seconds)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(fr)
        w.writeframes(b"\x00\x00" * n)


def _minimal_edit_plan(img: Path, bgm: Path, duration_s: float = 1.0) -> EditPlan:
    clip = ClipSpec(
        index=0,
        image_path=img,
        duration_s=duration_s,
        cut_at_s=0.0,
        camera=CameraMotion.ken_burns,
        transition_in=TransitionType.xfade,
        audio_event=AudioEvent.none,
        subtitle_position=SubtitlePosition.bottom,
    )
    return EditPlan(
        clips=[clip],
        lut_choice=LutChoice.epic_warm,
        end_plate_question="What would YOU have done?",
        narration_duration_s=duration_s,
        bgm_path=bgm,
    )


def test_build_ffmpeg_argv_shape(tmp_path: Path) -> None:
    img = tmp_path / "a.png"
    _write_min_png(img)
    wav = tmp_path / "n.wav"
    _write_min_wav(wav, 1.0)
    bgm = tmp_path / "b.wav"
    _write_min_wav(bgm, 2.0)
    ass = tmp_path / "s.ass"
    ass.write_text(
        "[Script Info]\nScriptType: v4.00+\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, "
        "SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, "
        "Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,2,2,10,10,60,1\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,Test\n",
        encoding="utf-8",
    )
    out = tmp_path / "out.mp4"

    settings = Settings(
        ffmpeg_path="ffmpeg",
        ffprobe_path="ffprobe",
        video_width=1080,
        video_height=1920,
        video_fps=30,
        watermark_enabled=False,
        end_plate_enabled=False,
        grain_strength=0,
        vignette_angle=0.0,
    )
    edit = _minimal_edit_plan(img, bgm, 1.0)
    req = RenderRequest(
        edit=edit,
        narration_wav=wav,
        ass_path=ass,
        out_mp4=out,
        narration_duration_s=1.0,
    )
    argv = build_ffmpeg_argv(req, settings)
    assert argv[0] == "ffmpeg"
    assert "-filter_complex" in argv
    fc_idx = argv.index("-filter_complex") + 1
    fc = argv[fc_idx]
    # Ken Burns zoompan must be present
    assert "zoompan" in fc
    # ASS subtitles must be applied
    assert "ass=filename=" in fc
    # Output path at end
    assert str(out.resolve()) in argv[-1]


def test_build_ffmpeg_argv_two_clips_xfade(tmp_path: Path) -> None:
    """Two clips with an xfade transition should produce an xfade filter."""
    imgs = []
    for i in range(2):
        p = tmp_path / f"img{i}.png"
        _write_min_png(p)
        imgs.append(p)
    wav = tmp_path / "n.wav"
    _write_min_wav(wav, 3.0)
    bgm = tmp_path / "b.wav"
    _write_min_wav(bgm, 4.0)
    ass = tmp_path / "s.ass"
    ass.write_text(
        "[Script Info]\nScriptType: v4.00+\n\n[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,"
        "0,0,0,0,100,100,0,0,1,2,2,2,10,10,60,1\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:00.00,0:00:03.00,Default,,0,0,0,,Test\n",
        encoding="utf-8",
    )
    out = tmp_path / "out.mp4"

    clips = [
        ClipSpec(
            index=0,
            image_path=imgs[0],
            duration_s=1.5,
            cut_at_s=0.0,
            camera=CameraMotion.ken_burns,
            transition_in=TransitionType.xfade,
            audio_event=AudioEvent.none,
            subtitle_position=SubtitlePosition.bottom,
        ),
        ClipSpec(
            index=1,
            image_path=imgs[1],
            duration_s=1.5,
            cut_at_s=1.5,
            camera=CameraMotion.zoom_out,
            transition_in=TransitionType.xfade,
            audio_event=AudioEvent.none,
            subtitle_position=SubtitlePosition.bottom,
        ),
    ]
    edit = EditPlan(
        clips=clips,
        lut_choice=LutChoice.tragic_cold,
        end_plate_question="Would you have crossed?",
        narration_duration_s=3.0,
        bgm_path=bgm,
    )
    settings = Settings(
        ffmpeg_path="ffmpeg",
        ffprobe_path="ffprobe",
        watermark_enabled=False,
        end_plate_enabled=False,
        grain_strength=0,
        vignette_angle=0.0,
    )
    req = RenderRequest(edit=edit, narration_wav=wav, ass_path=ass, out_mp4=out, narration_duration_s=3.0)
    argv = build_ffmpeg_argv(req, settings)
    fc = argv[argv.index("-filter_complex") + 1]
    assert "xfade" in fc
    assert "zoompan" in fc
    # Xfade overlaps clips; renderer compensates by extending available clips
    # before the transition chain, not by freezing the last frame.
    assert transition_chain_duration_s(clips) < req.narration_duration_s - 0.01
    assert "vpadnarr" not in fc


def test_compensate_xfade_overlap_spreads_missing_time_without_over_max(tmp_path: Path) -> None:
    img = tmp_path / "a.png"
    _write_min_png(img)
    clips = [
        ClipSpec(
            index=i,
            image_path=img,
            duration_s=3.0 if i < 3 else 2.5,
            cut_at_s=float(i * 3),
            camera=CameraMotion.ken_burns,
            transition_in=TransitionType.xfade if i else TransitionType.hard_cut,
            audio_event=AudioEvent.none,
            subtitle_position=SubtitlePosition.bottom,
        )
        for i in range(5)
    ]

    compensated = compensate_xfade_overlap(clips, narration_duration_s=sum(c.duration_s for c in clips))

    assert max(c.duration_s for c in compensated) <= 4.5
    assert transition_chain_duration_s(compensated) >= sum(c.duration_s for c in clips) - 0.01
