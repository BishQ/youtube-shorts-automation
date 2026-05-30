"""Tests for LTX 2.3 I2V helpers."""

from shorts_pipeline.video_worker.ltx_i2v import duration_to_ltx_seconds


def test_duration_clamps_to_whole_seconds() -> None:
    assert duration_to_ltx_seconds(4.2) == 5
    assert duration_to_ltx_seconds(0.5) == 2


def test_duration_respects_max() -> None:
    assert duration_to_ltx_seconds(99.0) == 10


def test_native_audio_patch_removes_external_mp3_branch() -> None:
    from shorts_pipeline.video_worker.ltx_i2v import _apply_ltx_pipeline_patch

    wf = {
        "109": {"inputs": {"audio_latent": ["376", 0]}},
        "140": {"inputs": {"audio": ["373", 0]}},
        "372": {"class_type": "LoadAudio"},
        "370": {"class_type": "MelBandRoFormerModelLoader"},
    }
    _apply_ltx_pipeline_patch(wf, mute_clip_audio=True)
    assert wf["109"]["inputs"]["audio_latent"] == ["199", 0]
    assert "audio" not in wf["140"]["inputs"]
    assert "372" not in wf
    assert "370" not in wf

    wf2 = {
        "109": {"inputs": {}},
        "140": {"inputs": {}},
        "372": {"class_type": "LoadAudio"},
    }
    _apply_ltx_pipeline_patch(wf2, mute_clip_audio=False)
    assert wf2["140"]["inputs"]["audio"] == ["201", 0]
