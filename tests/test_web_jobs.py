from pathlib import Path

from fastapi.testclient import TestClient

from shorts_pipeline.jobs.models import JobConfigSnapshot
from shorts_pipeline.jobs.store import JobStore
from shorts_pipeline.web import app as web_app


class _FakeRunner:
    def __init__(self) -> None:
        self.started: list[str] = []

    def start_job(self, job_id: str):
        self.started.append(job_id)

        class _Done:
            def add_done_callback(self, callback):
                return None

        return _Done()


def test_run_job_endpoint_uses_runner(tmp_path: Path, monkeypatch) -> None:
    store = JobStore(tmp_path / "jobs.sqlite")
    bgm = tmp_path / "bgm.wav"
    bgm.write_bytes(b"x")
    job_id = store.create_job(JobConfigSnapshot(figure_name="Web Run", bgm_path=str(bgm)))
    runner = _FakeRunner()

    monkeypatch.setattr(web_app, "_store", lambda: store)
    monkeypatch.setattr(web_app, "_runner", lambda: runner)

    response = TestClient(web_app.app).post(f"/api/jobs/{job_id}/run")

    assert response.status_code == 200
    assert runner.started == [job_id]
