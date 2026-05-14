import json
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from shorts_pipeline.config.settings import Settings
from shorts_pipeline.jobs.image_recovery import (
    reconcile_image_artifacts_from_disk,
    resolve_folder_to_job_id,
    scan_job_folder,
)
from shorts_pipeline.jobs.models import ArtifactType, JobConfigSnapshot, PipelineStage
from shorts_pipeline.jobs.store import JobStore


def test_default_bgm_from_settings_env(tmp_path: Path) -> None:
    from shorts_pipeline.jobs.image_recovery import default_bgm_path_for_reconcile

    bgm = tmp_path / "loop.wav"
    bgm.write_bytes(b"x" * 200)
    s = Settings.model_construct(default_bgm_path=str(bgm))
    assert default_bgm_path_for_reconcile(s) == str(bgm.resolve())


def test_resolve_folder_bare_job_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = tmp_path / "data"
    jobs = data / "jobs" / "ab-cd-ef123456"
    jobs.mkdir(parents=True)
    s = Settings.model_construct(data_dir=data)
    monkeypatch.setattr("shorts_pipeline.config.settings.get_settings", lambda: s)
    jid = resolve_folder_to_job_id(s, "ab-cd-ef123456")
    assert jid == "ab-cd-ef123456"


def test_scan_and_reconcile(tmp_path: Path) -> None:
    data = tmp_path / "data"
    db = tmp_path / "j.sqlite"
    store = JobStore(db)
    bgm = tmp_path / "bgm.wav"
    bgm.write_bytes(b"x" * 512)
    cfg = JobConfigSnapshot(figure_name="Fig", bgm_path=str(bgm))
    jid = store.create_job(cfg)
    root = data / "jobs" / jid
    img = root / "images"
    img.mkdir(parents=True, exist_ok=True)
    plan = {
        "historical_figure": "Fig",
        "cold_open_object": "x",
        "decision_lever": {"lever_type": "x", "description": "d", "consequence": "c"},
        "clauses": [
            {"text": "a", "image_prompt": "p0"},
            {"text": "b", "image_prompt": "p1"},
        ],
        "full_script": "a b",
    }
    (root / "plan.json").write_text(json.dumps(plan), encoding="utf-8")

    def _png_bytes() -> bytes:
        bio = BytesIO()
        Image.new("RGB", (128, 128), color=(40, 60, 80)).save(bio, format="PNG")
        return bio.getvalue()

    p0 = img / "clause_000.png"
    p0.write_bytes(_png_bytes())
    p1 = img / "clause_001.png"
    p1.write_bytes(_png_bytes())

    s = Settings.model_construct(data_dir=data)
    info = scan_job_folder(s, jid)
    assert info["plan_clause_count"] == 2
    assert info["valid_clause_png_on_disk"] == 2

    rec = reconcile_image_artifacts_from_disk(store, s, jid)
    assert rec["registered_from_disk"] == 2
    assert rec["artifact_rows_for_images"] == 2
    arts = store.get_artifacts_for_stage(jid, PipelineStage.images, ArtifactType.image_png)
    assert len(arts) == 2


def test_import_job_if_missing_and_reconcile(tmp_path: Path) -> None:
    from io import BytesIO

    from PIL import Image

    from shorts_pipeline.jobs.image_recovery import (
        build_job_config_for_disk_import,
        ensure_plan_artifact_from_disk,
        reconcile_image_artifacts_from_disk,
    )

    data = tmp_path / "data"
    db = tmp_path / "imp.sqlite"
    store = JobStore(db)
    bgm = tmp_path / "bgm.wav"
    bgm.write_bytes(b"x" * 512)
    jid = "hero-test-ab12cd34"
    root = data / "jobs" / jid
    img = root / "images"
    img.mkdir(parents=True, exist_ok=True)
    plan = {
        "historical_figure": "Hero Test",
        "cold_open_object": "x",
        "decision_lever": {"lever_type": "x", "description": "d", "consequence": "c"},
        "clauses": [
            {"text": "a", "image_prompt": "p0"},
            {"text": "b", "image_prompt": "p1"},
        ],
        "full_script": "a b",
    }
    (root / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    bio = BytesIO()
    Image.new("RGB", (128, 128), color=(10, 20, 30)).save(bio, format="PNG")
    bpng = bio.getvalue()
    (img / "clause_000.png").write_bytes(bpng)
    (img / "clause_001.png").write_bytes(bpng)

    s = Settings.model_construct(data_dir=data)
    cfg = build_job_config_for_disk_import(s, jid, str(bgm))
    assert store.import_job_if_missing(jid, cfg) is True
    assert store.get_job(jid) is not None
    ensure_plan_artifact_from_disk(store, s, jid)
    rec = reconcile_image_artifacts_from_disk(store, s, jid)
    assert rec["registered_from_disk"] == 2
    assert store.get_latest_artifact(jid, PipelineStage.plan, ArtifactType.plan_json) is not None
