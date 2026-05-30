from pathlib import Path

from shorts_pipeline.config.settings import Settings
from shorts_pipeline.jobs.models import JobConfigSnapshot
from shorts_pipeline.jobs.pipeline_runner import PipelineRunner
from shorts_pipeline.jobs.store import JobStore


def test_pipeline_runner_uses_effective_settings_for_stage_runner(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = JobStore(tmp_path / "jobs.sqlite")
    bgm = tmp_path / "bgm.wav"
    bgm.write_bytes(b"x")
    job_id = store.create_job(JobConfigSnapshot(figure_name="Runner Test", bgm_path=str(bgm)))
    settings = Settings.model_construct(data_dir=tmp_path, i2v_enabled=False)
    seen: dict[str, object] = {}

    class FakeStageRunner:
        def __init__(self, got_store, handler, *, settings=None) -> None:
            seen["store"] = got_store
            seen["handler"] = handler
            seen["settings"] = settings

        def resume_job(self, got_job_id: str, *, stop_after=None) -> None:
            seen["job_id"] = got_job_id
            seen["stop_after"] = stop_after

    monkeypatch.setattr("shorts_pipeline.jobs.pipeline_runner.StageRunner", FakeStageRunner)
    monkeypatch.setattr(
        "shorts_pipeline.jobs.pipeline_runner.orchestrator_stage_handler",
        lambda got_settings, got_store: {"settings": got_settings, "store": got_store},
    )

    PipelineRunner(settings, store).run_job(job_id)

    assert seen["job_id"] == job_id
    assert seen["store"] is store
    assert seen["settings"] is not None
