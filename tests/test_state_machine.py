import json
from pathlib import Path

import pytest

from shorts_pipeline.jobs.models import ArtifactType, JobConfigSnapshot, PipelineStage
from shorts_pipeline.jobs.state_machine import StageRunner
from shorts_pipeline.jobs.store import JobStore


def test_plan_stage_skipped_when_artifact_valid(tmp_path: Path) -> None:
    db = tmp_path / "jobs.sqlite"
    store = JobStore(db)
    bgm = tmp_path / "bgm.wav"
    bgm.write_bytes(b"x" * 512)
    cfg = JobConfigSnapshot(figure_name="Test Figure", bgm_path=str(bgm))
    jid = store.create_job(cfg)

    state = {"plan_called": False}

    class H:
        def run_plan(self, job_id: str) -> None:
            state["plan_called"] = True

        def run_images(self, job_id: str) -> None:
            raise RuntimeError("stop_after_plan_skip_check")

        def run_tts(self, job_id: str) -> None:
            raise RuntimeError("unexpected")

        def run_align(self, job_id: str) -> None:
            raise RuntimeError("unexpected")

        def run_render(self, job_id: str) -> None:
            raise RuntimeError("unexpected")

    jd = tmp_path / "jobs" / jid
    jd.mkdir(parents=True, exist_ok=True)
    plan_path = jd / "plan.json"
    plan = {
        "historical_figure": "Test Figure",
        "cold_open_object": "bronze coin",
        "decision_lever": {
            "lever_type": "politics",
            "description": "A political decision that changed alliances",
            "consequence": "It shifted trade routes and weakened the old elite.",
        },
        "clauses": [
            {"text": "One cold open clause here.", "image_prompt": "ancient coin macro photo"},
            {"text": "Second clause continues the beat.", "image_prompt": "map on table"},
            {"text": "Third clause raises tension.", "image_prompt": "crowd in agora"},
            {"text": "Fourth clause lands the consequence.", "image_prompt": "ruins at sunset"},
        ],
        "full_script": "One cold open clause here. Second clause continues the beat. "
        "Third clause raises tension. Fourth clause lands the consequence.",
    }
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    store.add_artifact(jid, PipelineStage.plan, ArtifactType.plan_json, plan_path)

    runner = StageRunner(store, H())
    with pytest.raises(RuntimeError, match="stop_after_plan_skip_check"):
        runner.resume_job(jid)

    assert state["plan_called"] is False


def test_images_stage_not_complete_when_fewer_artifacts_than_clauses(tmp_path: Path) -> None:
    """Regression: partial image set must not satisfy the images gate."""
    db = tmp_path / "jobs.sqlite"
    store = JobStore(db)
    bgm = tmp_path / "bgm.wav"
    bgm.write_bytes(b"x" * 512)
    cfg = JobConfigSnapshot(figure_name="Img Gate", bgm_path=str(bgm))
    jid = store.create_job(cfg)

    jd = tmp_path / "jobs" / jid
    jd.mkdir(parents=True, exist_ok=True)
    plan_path = jd / "plan.json"
    plan = {
        "historical_figure": "Img Gate",
        "cold_open_object": "x",
        "decision_lever": {"lever_type": "x", "description": "d", "consequence": "c"},
        "clauses": [
            {"text": "a", "image_prompt": "p0"},
            {"text": "b", "image_prompt": "p1"},
            {"text": "c", "image_prompt": "p2"},
        ],
        "full_script": "a b c",
    }
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    store.add_artifact(jid, PipelineStage.plan, ArtifactType.plan_json, plan_path)

    img0 = jd / "images" / "clause_000.png"
    img0.parent.mkdir(parents=True, exist_ok=True)
    img0.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 80)

    store.add_artifact(jid, PipelineStage.images, ArtifactType.image_png, img0)

    state = {"images_called": False}

    class H:
        def run_plan(self, job_id: str) -> None:
            raise RuntimeError("unexpected")

        def run_images(self, job_id: str) -> None:
            state["images_called"] = True
            raise RuntimeError("stop_after_images_invoked")

        def run_tts(self, job_id: str) -> None:
            raise RuntimeError("unexpected")

        def run_align(self, job_id: str) -> None:
            raise RuntimeError("unexpected")

        def run_render(self, job_id: str) -> None:
            raise RuntimeError("unexpected")

    runner = StageRunner(store, H())
    with pytest.raises(RuntimeError, match="stop_after_images_invoked"):
        runner.resume_job(jid)

    assert state["images_called"] is True
