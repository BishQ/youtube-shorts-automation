from concurrent.futures import Future
from pathlib import Path

from shorts_pipeline.config.settings import Settings
from shorts_pipeline.jobs.batch_store import BatchStore
from shorts_pipeline.jobs.models import JobStatus, PipelineStage
from shorts_pipeline.jobs.store import JobStore
from shorts_pipeline.web.services.batch_scheduler import BatchScheduler


class _FakeRunner:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self.started: list[str] = []

    def effective_settings(self) -> Settings:
        return self._settings

    def start_job(self, job_id: str) -> Future[None]:
        self.started.append(job_id)
        fut: Future[None] = Future()
        fut.set_result(None)
        return fut


def test_batch_scheduler_advances_index_only_after_job_completed(tmp_path: Path) -> None:
    db = tmp_path / "jobs.sqlite"
    store = JobStore(db)
    batch_store = BatchStore(db)
    bgm = tmp_path / "bgm.wav"
    bgm.write_bytes(b"x")
    settings = Settings.model_construct(
        data_dir=tmp_path,
        watermark_enabled=False,
        end_plate_enabled=True,
        comfy_workflow_name="workflow",
        overlay_enabled=True,
    )
    runner = _FakeRunner(settings)
    scheduler = BatchScheduler(
        settings=settings,
        store=store,
        batch_store=batch_store,
        runner=runner,  # type: ignore[arg-type]
    )
    batch = batch_store.create("batch", ["Topic A"], str(bgm))
    batch_store.set_status(batch.id, "running")

    scheduler.tick_active()
    active = batch_store.get(batch.id)
    assert active is not None
    assert active.current_index == 0
    assert active.job_ids[0] is not None
    assert runner.started == [active.job_ids[0]]

    store.update_job_progress(
        active.job_ids[0],
        status=JobStatus.completed,
        last_completed_stage=PipelineStage.publish,
        clear_current_stage=True,
    )
    scheduler.tick_active()

    done = batch_store.get(batch.id)
    assert done is not None
    assert done.current_index == 1
