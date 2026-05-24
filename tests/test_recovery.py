from pathlib import Path

from shorts_pipeline.jobs.models import JobConfigSnapshot, JobStatus, PipelineStage
from shorts_pipeline.jobs.recovery import recover_interrupted_jobs
from shorts_pipeline.jobs.store import JobStore


def test_recover_interrupted_running_job_marks_failed_and_closes_timing(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite")
    bgm = tmp_path / "bgm.wav"
    bgm.write_bytes(b"x")
    job_id = store.create_job(JobConfigSnapshot(figure_name="Crash Test", bgm_path=str(bgm)))
    store.update_job_progress(job_id, status=JobStatus.running, current_stage=PipelineStage.images)
    store.record_stage_start(job_id, PipelineStage.images, "Images")

    recovered = recover_interrupted_jobs(store)

    assert [r.job_id for r in recovered] == [job_id]
    job = store.get_job(job_id)
    assert job is not None
    assert job.status == JobStatus.failed
    assert job.error is not None
    assert job.error.code == "Interrupted"
    timings = store.get_stage_timings_for_job(job_id)
    assert timings[0]["active"] is False


def test_recover_interrupted_jobs_preserves_paused_jobs(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite")
    bgm = tmp_path / "bgm.wav"
    bgm.write_bytes(b"x")
    job_id = store.create_job(JobConfigSnapshot(figure_name="Paused Test", bgm_path=str(bgm)))
    store.update_job_progress(job_id, status=JobStatus.paused, current_stage=PipelineStage.images)

    assert recover_interrupted_jobs(store) == []
    job = store.get_job(job_id)
    assert job is not None
    assert job.status == JobStatus.paused
