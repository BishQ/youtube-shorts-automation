import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from shorts_pipeline.config.settings import Settings
from shorts_pipeline.renderer.quality import (
    RenderQualityError,
    validate_ass_for_render,
    validate_render_output,
)


def _write_ass(path: Path, *, old_header: bool = False) -> None:
    event_format = (
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, Effect, Text"
        if old_header
        else "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
    )
    path.write_text(
        "[Script Info]\n"
        "ScriptType: v4.00+\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,"
        "0,0,0,0,100,100,0,0,1,2,2,2,10,10,60,1\n"
        "[Events]\n"
        f"{event_format}\n"
        "Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,Test\n",
        encoding="utf-8",
    )


def _ffprobe_payload(*, duration: float, width: int = 1080, height: int = 1920) -> str:
    return json.dumps(
        {
            "streams": [
                {
                    "codec_type": "video",
                    "width": width,
                    "height": height,
                    "r_frame_rate": "30/1",
                },
                {"codec_type": "audio", "r_frame_rate": "0/0"},
            ],
            "format": {"duration": str(duration), "size": "250000"},
        }
    )


def test_validate_ass_rejects_header_that_renders_leading_comma(tmp_path: Path) -> None:
    ass = tmp_path / "bad.ass"
    _write_ass(ass, old_header=True)

    with pytest.raises(RenderQualityError, match="missing MarginV"):
        validate_ass_for_render(ass)


def test_validate_render_output_rejects_duration_explosion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ass = tmp_path / "ok.ass"
    _write_ass(ass)
    mp4 = tmp_path / "out.mp4"
    mp4.write_bytes(b"0" * 250000)

    def fake_run(*args: object, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(returncode=0, stdout=_ffprobe_payload(duration=1877.3), stderr="")

    monkeypatch.setattr("shorts_pipeline.renderer.quality.subprocess.run", fake_run)

    with pytest.raises(RenderQualityError, match="wrong duration"):
        validate_render_output(
            mp4_path=mp4,
            ass_path=ass,
            expected_duration_s=52.0,
            settings=Settings(),
        )


def test_validate_render_output_accepts_good_vertical_short(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ass = tmp_path / "ok.ass"
    _write_ass(ass)
    mp4 = tmp_path / "out.mp4"
    mp4.write_bytes(b"0" * 250000)

    def fake_run(*args: object, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(returncode=0, stdout=_ffprobe_payload(duration=52.2), stderr="")

    monkeypatch.setattr("shorts_pipeline.renderer.quality.subprocess.run", fake_run)

    probe = validate_render_output(
        mp4_path=mp4,
        ass_path=ass,
        expected_duration_s=52.0,
        settings=Settings(),
    )

    assert probe.width == 1080
    assert probe.height == 1920
    assert probe.has_audio
