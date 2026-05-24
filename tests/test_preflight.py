from pathlib import Path

from shorts_pipeline.config.settings import Settings
from shorts_pipeline.jobs import preflight


def test_run_preflight_returns_failed_report_for_unreachable_services(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        preflight,
        "_check_executable",
        lambda name, executable: preflight.CheckResult(name, True, executable),
    )
    monkeypatch.setattr(
        preflight,
        "_check_http_json",
        lambda name, url, timeout_s=3.0: preflight.CheckResult(name, False, url),
    )
    settings = Settings.model_construct(
        data_dir=tmp_path,
        ffmpeg_path="ffmpeg",
        ffprobe_path="ffprobe",
        local_llm_base_url="http://127.0.0.1:9/v1",
        comfy_base_url="http://127.0.0.1:9",
        tts_backend="kokoro",
    )

    report = preflight.run_preflight(settings)

    assert report.ok is False
    failed = {c.name for c in report.checks if not c.ok}
    assert failed == {"vllm", "comfyui"}
