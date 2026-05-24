from fastapi.testclient import TestClient

from shorts_pipeline.jobs.preflight import CheckResult, PreflightReport
from shorts_pipeline.web import app as web_app


def test_health_ready_returns_503_when_preflight_fails(monkeypatch) -> None:
    monkeypatch.setattr(
        web_app,
        "run_preflight",
        lambda settings: PreflightReport(
            ok=False,
            checks=[CheckResult("vllm", False, "down")],
        ),
    )

    response = TestClient(web_app.app).get("/api/health/ready")

    assert response.status_code == 503
    assert response.json()["checks"][0]["name"] == "vllm"
