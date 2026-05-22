"""FastAPI server: jobs API, batch queue, config, logs, static UI."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from shorts_pipeline.config.settings import Settings, get_settings
from shorts_pipeline.config.ui_store import effective_settings, get_ui_store
from shorts_pipeline.context import set_job_id
from shorts_pipeline.jobs.batch_store import BatchStore, parse_topics
from shorts_pipeline.jobs.image_recovery import (
    build_job_config_for_disk_import,
    default_bgm_path_for_reconcile,
    delete_clause_for_regen,
    ensure_plan_artifact_from_disk,
    read_import_bgm_hint,
    reconcile_image_artifacts_from_disk,
    resolve_folder_to_job_id,
    scan_job_folder,
)
from shorts_pipeline.jobs.models import ArtifactType, JobConfigSnapshot, JobStatus, PipelineStage
from shorts_pipeline.jobs.state_machine import StageRunner
from shorts_pipeline.jobs.store import JobStore
from shorts_pipeline.logging_setup import configure_logging, get_logger
from shorts_pipeline.orchestrator import PipelineOrchestrator, orchestrator_stage_handler
from shorts_pipeline.web.log_buffer import job_log_buffer
from shorts_pipeline.web.structlog_handler import JobLogHandler

log = get_logger(__name__)

app = FastAPI(title="shorts-pipeline", version="0.1.0")

_static_dir = Path(__file__).resolve().parent / "static"
if _static_dir.is_dir():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

_settings_singleton: Settings | None = None
_store_singleton: JobStore | None = None
_batch_store_singleton: BatchStore | None = None
_log_handler_attached = False


def _settings() -> Settings:
    global _settings_singleton
    if _settings_singleton is None:
        _settings_singleton = get_settings()
    return _settings_singleton


def _store() -> JobStore:
    global _store_singleton
    if _store_singleton is None:
        s = _settings()
        _store_singleton = JobStore(s.data_dir / "jobs.sqlite")
    return _store_singleton


def _batch_store() -> BatchStore:
    global _batch_store_singleton
    if _batch_store_singleton is None:
        s = _settings()
        _batch_store_singleton = BatchStore(s.data_dir / "jobs.sqlite")
    return _batch_store_singleton


# ── Pipeline runner ───────────────────────────────────────────────────────────

def _run_pipeline(job_id: str) -> None:
    set_job_id(job_id)
    base = _settings()
    s = effective_settings(base)
    h = orchestrator_stage_handler(s, _store())
    StageRunner(_store(), h, settings=_settings()).resume_job(job_id)


# ── Batch duplicate guard ─────────────────────────────────────────────────────

def _check_topic_duplicate(figure_name: str) -> str | None:
    """
    Before creating a new job for *figure_name*, inspect existing jobs:

    - If a job with a valid final.mp4 exists → return its job_id (caller should skip).
    - If only failed / incomplete jobs exist → delete them and return None (caller creates fresh).
    - If nothing exists → return None (caller creates fresh).
    """
    store = _store()
    existing = store.find_jobs_by_figure(figure_name)
    skip_job_id: str | None = None

    for job in existing:
        art = store.get_latest_artifact(job.id, PipelineStage.render, ArtifactType.final_mp4)
        if art and Path(art.path).is_file() and Path(art.path).stat().st_size > 0:
            # Completed with a real final.mp4 — remember this and stop looking
            skip_job_id = job.id
            break

    if skip_job_id:
        log.info(
            "batch_topic_skip_duplicate",
            figure=figure_name,
            existing_job_id=skip_job_id,
        )
        return skip_job_id

    # No usable final.mp4 — clean up stale failed/incomplete jobs so the DB
    # doesn't accumulate zombie rows for the same figure.
    for job in existing:
        if job.status in (JobStatus.failed,):
            log.info("batch_stale_job_removed", figure=figure_name, job_id=job.id)
            store.delete_job(job.id)

    return None


# ── Batch watcher (auto-advances queue after each job finishes) ───────────────

async def _tick_batch() -> None:
    store = _store()
    bs = _batch_store()

    batch = bs.get_active()
    if batch is None or batch.status != "running":
        return

    # Still processing a job — wait
    if store.has_running_job():
        return

    # Check if the previous job failed (auto-pause on failure)
    prev_idx = batch.current_index - 1
    if 0 <= prev_idx < len(batch.job_ids):
        prev_jid = batch.job_ids[prev_idx]
        if prev_jid:
            prev_job = store.get_job(prev_jid)
            if prev_job and prev_job.status == JobStatus.failed:
                cancelled = prev_job.error and "Cancelled" in (prev_job.error.message or "")
                if not cancelled:
                    bs.set_status(batch.id, "paused")
                    log.warning("batch_auto_paused", reason="job_failed",
                                job_id=prev_jid, index=prev_idx)
                    return

    # All topics done?
    idx = batch.current_index
    if idx >= len(batch.topics):
        bs.set_status(batch.id, "completed")
        log.info("batch_completed", batch_id=batch.id, total=len(batch.topics))
        return

    # Start the next job
    topic = batch.topics[idx]
    bgm = Path(batch.bgm_path)
    if not bgm.is_file():
        bs.set_status(batch.id, "paused")
        log.error("batch_paused_bgm_missing", bgm=str(bgm))
        return

    # Duplicate guard: skip if final.mp4 already exists, delete stale failed jobs otherwise
    existing_jid = _check_topic_duplicate(topic)
    if existing_jid:
        bs.record_job(batch.id, idx, existing_jid)
        bs.set_current_index(batch.id, idx + 1)
        log.info("batch_topic_skipped", batch_id=batch.id, topic=topic, index=idx, job_id=existing_jid)
        return  # watcher will immediately advance on next tick

    s = get_settings()
    cfg = JobConfigSnapshot(
        figure_name=topic,
        bgm_path=str(bgm.resolve()),
        topic_type=batch.topic_type,
        language=batch.language,
        watermark_enabled=s.watermark_enabled,
        end_plate_enabled=s.end_plate_enabled,
    )
    jid = store.create_job(cfg)
    bs.record_job(batch.id, idx, jid)
    bs.set_current_index(batch.id, idx + 1)

    loop = asyncio.get_event_loop()
    fut = loop.run_in_executor(None, _run_pipeline, jid)

    def _sink_executor_exc(f: asyncio.Future[object]) -> None:
        try:
            f.result()
        except BaseException:
            pass

    fut.add_done_callback(_sink_executor_exc)
    log.info("batch_job_started", batch_id=batch.id, topic=topic, index=idx, job_id=jid)


async def _batch_watcher() -> None:
    while True:
        await asyncio.sleep(5)
        try:
            await _tick_batch()
        except Exception as exc:
            log.warning("batch_watcher_error", error=str(exc))


# ── Startup ───────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def _startup() -> None:
    global _log_handler_attached
    s = _settings()
    configure_logging(level=s.log_level, json_logs=s.log_json)
    if not _log_handler_attached:
        root = logging.getLogger()
        root.addHandler(JobLogHandler())
        _log_handler_attached = True
    asyncio.create_task(_batch_watcher())


# ── Request bodies ────────────────────────────────────────────────────────────

class CreateJobBody(BaseModel):
    figure_name: str = Field(..., min_length=1)
    bgm_path: str = Field(..., min_length=1)
    topic_type: str = "historical_figure"
    language: str = "en"
    watermark_enabled: bool = Field(default_factory=lambda: get_settings().watermark_enabled)
    end_plate_enabled: bool = Field(default_factory=lambda: get_settings().end_plate_enabled)
    comfy_workflow_name: str | None = None
    overlay_enabled: bool = True


class UiPatchBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    comfy_workflow_name: str | None = None
    whisper_model_size: str | None = None


class CreateBatchBody(BaseModel):
    name: str = Field(..., min_length=1)
    topics: list[str] = Field(..., min_items=1)
    bgm_path: str = Field(..., min_length=1)
    topic_type: str = "historical_figure"
    language: str = "en"


class StartBatchBody(BaseModel):
    from_index: int = Field(default=0, ge=0)


# ── General endpoints ─────────────────────────────────────────────────────────

@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/gemini/usage")
def gemini_usage() -> dict[str, object]:
    """Legacy endpoint — kept so the UI keeps functioning.

    The pipeline now runs entirely on local Ollama; there is no remote
    quota to report.  Returns a minimal status payload.
    """
    s = _settings()
    return {
        "planner_backend": s.planner_backend,
        "local_llm_model": s.local_llm_model,
        "local_llm_base_url": s.local_llm_base_url,
        "note": "Pipeline runs on local Ollama — no remote rate limits.",
    }


@app.get("/api/config")
def get_config() -> dict:
    s = _settings()
    ui = get_ui_store().load()
    return {
        "env": {
            "comfy_base_url": s.comfy_base_url,
            "tts_backend": s.tts_backend,
            "kokoro_voice": s.kokoro_voice,
            "kokoro_http_base_url": s.kokoro_http_base_url,
            "data_dir": str(s.data_dir),
            "workflows_dir": str(s.workflows_dir),
            "video": {"width": s.video_width, "height": s.video_height, "fps": s.video_fps},
            "default_bgm_path": default_bgm_path_for_reconcile(s),
            "job_defaults": {
                "watermark_enabled": s.watermark_enabled,
                "end_plate_enabled": s.end_plate_enabled,
            },
        },
        "ui": ui.model_dump(),
    }


@app.put("/api/config")
def put_config(body: UiPatchBody) -> dict:
    m = get_ui_store().save_patch(body.model_dump(exclude_none=True))
    return m.model_dump()


# ── Job endpoints ─────────────────────────────────────────────────────────────

@app.post("/api/jobs")
def create_job(body: CreateJobBody) -> dict:
    if body.topic_type != "historical_figure":
        raise HTTPException(status_code=400, detail="V1 only supports topic_type=historical_figure")
    if body.language != "en":
        raise HTTPException(status_code=400, detail="V1 only supports language=en")
    bgm = Path(body.bgm_path)
    if not bgm.is_file():
        raise HTTPException(status_code=400, detail=f"BGM path is not a file: {body.bgm_path}")

    cfg = JobConfigSnapshot(
        figure_name=body.figure_name,
        topic_type=body.topic_type,
        language=body.language,
        bgm_path=str(bgm.resolve()),
        watermark_enabled=body.watermark_enabled,
        end_plate_enabled=body.end_plate_enabled,
        comfy_workflow_name=body.comfy_workflow_name,
        overlay_enabled=body.overlay_enabled,
    )
    jid = _store().create_job(cfg)
    log.info("job_created", job_id=jid)
    return {"id": jid}


@app.get("/api/jobs")
def list_jobs() -> list[dict]:
    out = []
    for j in _store().list_jobs():
        out.append({
            "id": j.id,
            "figure_name": j.figure_name,
            "status": j.status.value,
            "current_stage": j.current_stage.value if j.current_stage else None,
            "last_completed_stage": j.last_completed_stage.value if j.last_completed_stage else None,
            "error": j.error.model_dump() if j.error else None,
            "created_at": j.created_at.isoformat(),
        })
    return out


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    store = _store()
    j = store.get_job(job_id)
    if j is None:
        raise HTTPException(status_code=404, detail="job not found")
    timings = store.get_stage_timings_for_job(job_id)
    total_seconds = round(sum(float(t["duration_seconds"]) for t in timings), 2)
    return {
        "id": j.id,
        "figure_name": j.figure_name,
        "status": j.status.value,
        "current_stage": j.current_stage.value if j.current_stage else None,
        "last_completed_stage": j.last_completed_stage.value if j.last_completed_stage else None,
        "error": j.error.model_dump() if j.error else None,
        "config": j.config_snapshot.model_dump(),
        "stage_timings": timings,
        "timing_total_seconds": total_seconds,
        "timing_human": JobStore._humanize_seconds(total_seconds),
    }


class FolderPathBody(BaseModel):
    folder_path: str = Field(..., min_length=2, max_length=2048)


class ReconcileFolderBody(BaseModel):
    folder_path: str = Field(..., min_length=2, max_length=2048)
    bgm_path: str | None = Field(None, max_length=4096)


def _job_must_not_be_running_for_image_edit(j) -> None:
    if j.status == JobStatus.running:
        raise HTTPException(
            status_code=409,
            detail="wait for the job to finish or cancel it before changing clause images",
        )


def _plan_clauses_list(job_id: str) -> tuple[list[dict], int]:
    store = _store()
    s = _settings()
    art = store.get_latest_artifact(job_id, PipelineStage.plan, ArtifactType.plan_json)
    if art and Path(art.path).is_file():
        data = json.loads(Path(art.path).read_text(encoding="utf-8"))
    else:
        p = s.data_dir / "jobs" / job_id / "plan.json"
        if not p.is_file():
            raise HTTPException(status_code=404, detail="plan.json not found — cannot list clauses")
        data = json.loads(p.read_text(encoding="utf-8"))
    clauses = data.get("clauses")
    if not isinstance(clauses, list):
        raise HTTPException(status_code=422, detail="plan has no clauses array")
    return clauses, len(clauses)


@app.post("/api/jobs/scan-folder")
def scan_job_folder_endpoint(body: FolderPathBody) -> JSONResponse:
    s = _settings()
    store = _store()
    try:
        jid = resolve_folder_to_job_id(s, body.folder_path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    info = scan_job_folder(s, jid)
    in_db = store.get_job(jid) is not None
    return JSONResponse({"job_id": jid, "in_database": in_db, **info})


@app.post("/api/jobs/reconcile-folder")
def reconcile_job_folder_endpoint(body: ReconcileFolderBody) -> JSONResponse:
    store = _store()
    s = _settings()
    try:
        jid = resolve_folder_to_job_id(s, body.folder_path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    root = s.data_dir / "jobs" / jid
    imported_from_disk = False
    j = store.get_job(jid)
    if j is None:
        bgm = (body.bgm_path or "").strip() or read_import_bgm_hint(root) or default_bgm_path_for_reconcile(s)
        if not bgm:
            raise HTTPException(
                status_code=400,
                detail=(
                    "This job folder is not in the SQLite database yet. Set BGM: use the field in the UI, "
                    'import_meta.json with {"bgm_path": "..."}, environment SHORTS_DEFAULT_BGM_PATH, '
                    "or place extra tools/bgm.mp3 (or .wav) next to your project."
                ),
            )
        try:
            cfg = build_job_config_for_disk_import(s, jid, bgm)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        imported_from_disk = store.import_job_if_missing(jid, cfg)
        j = store.get_job(jid)
        if j is None:
            raise HTTPException(status_code=500, detail="could not import job row")
        if imported_from_disk:
            log.info("job_imported_from_disk", job_id=jid)
    assert j is not None
    _job_must_not_be_running_for_image_edit(j)
    ensure_plan_artifact_from_disk(store, s, jid)
    try:
        info = reconcile_image_artifacts_from_disk(store, s, jid)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if info["images_stage_complete"]:
        store.update_job_progress(
            jid,
            status=JobStatus.paused,
            clear_error=True,
            last_completed_stage=PipelineStage.images,
            current_stage=PipelineStage.tts,
        )
    else:
        store.update_job_progress(
            jid,
            status=JobStatus.paused,
            clear_error=True,
            last_completed_stage=PipelineStage.plan,
            current_stage=PipelineStage.images,
        )
    log.info("job_images_reconciled", job_id=jid, registered=info.get("registered_from_disk"))
    return JSONResponse({"job_id": jid, "imported_from_disk": imported_from_disk, **info})


@app.get("/api/jobs/{job_id}/images/review")
def images_review(job_id: str) -> dict:
    store = _store()
    if store.get_job(job_id) is None:
        raise HTTPException(status_code=404, detail="job not found")
    clauses, n = _plan_clauses_list(job_id)
    by_name = {
        Path(a.path).name: a
        for a in store.get_artifacts_for_stage(job_id, PipelineStage.images, ArtifactType.image_png)
    }
    img_dir = _settings().data_dir / "jobs" / job_id / "images"
    out: list[dict] = []
    for i, cl in enumerate(clauses):
        if not isinstance(cl, dict):
            cl = {}
        name = f"clause_{i:03d}.png"
        art = by_name.get(name)
        p = img_dir / name
        pr = cl.get("image_prompt") or ""
        if not isinstance(pr, str):
            pr = ""
        meta: dict = {}
        if art and art.meta_json:
            try:
                parsed = json.loads(art.meta_json)
                if isinstance(parsed, dict):
                    meta = parsed
            except Exception:
                pass
        out.append(
            {
                "index": i,
                "filename": name,
                "prompt_preview": pr[:220],
                "has_artifact": art is not None,
                "has_readable_file": p.is_file() and p.stat().st_size > 64,
                "artifact_meta": meta,
                "thumb_url": f"/api/jobs/{job_id}/images/{i}/file",
            }
        )
    return {"clause_count": n, "clauses": out}


@app.get("/api/jobs/{job_id}/images/{clause_index}/file")
def download_clause_png(job_id: str, clause_index: int) -> FileResponse:
    if clause_index < 0:
        raise HTTPException(status_code=422, detail="invalid index")
    store = _store()
    if store.get_job(job_id) is None:
        raise HTTPException(status_code=404, detail="job not found")
    p = _settings().data_dir / "jobs" / job_id / "images" / f"clause_{clause_index:03d}.png"
    if not p.is_file():
        raise HTTPException(status_code=404, detail="image not found")
    return FileResponse(p, media_type="image/png", filename=p.name)


@app.post("/api/jobs/{job_id}/images/{clause_index}/retry")
def retry_clause_image(job_id: str, clause_index: int) -> JSONResponse:
    store = _store()
    j = store.get_job(job_id)
    if j is None:
        raise HTTPException(status_code=404, detail="job not found")
    _job_must_not_be_running_for_image_edit(j)
    _, n = _plan_clauses_list(job_id)
    if clause_index < 0 or clause_index >= n:
        raise HTTPException(status_code=422, detail="clause_index out of range")
    eff = effective_settings(_settings())
    info = delete_clause_for_regen(
        store,
        _settings(),
        job_id,
        clause_index,
        image_backend=eff.image_backend.lower().strip(),
    )
    store.update_job_progress(
        job_id,
        status=JobStatus.paused,
        clear_error=True,
        last_completed_stage=PipelineStage.plan,
        current_stage=PipelineStage.images,
    )
    log.info("clause_image_retry_prepared", job_id=job_id, clause=clause_index)
    return JSONResponse({"ok": True, **info})


@app.post("/api/jobs/{job_id}/images/{clause_index}/approve")
def approve_clause_image(job_id: str, clause_index: int) -> JSONResponse:
    store = _store()
    if store.get_job(job_id) is None:
        raise HTTPException(status_code=404, detail="job not found")
    ok = store.merge_image_meta_for_clause(job_id, clause_index, {"review": "approved"})
    return JSONResponse({"ok": ok})


@app.post("/api/jobs/{job_id}/images/{clause_index}/replace")
async def replace_clause_image_upload(
    job_id: str, clause_index: int, file: UploadFile = File(...)
) -> JSONResponse:
    store = _store()
    j = store.get_job(job_id)
    if j is None:
        raise HTTPException(status_code=404, detail="job not found")
    _job_must_not_be_running_for_image_edit(j)
    _, n = _plan_clauses_list(job_id)
    if clause_index < 0 or clause_index >= n:
        raise HTTPException(status_code=422, detail="clause_index out of range")
    raw = await file.read()
    if len(raw) < 64 or not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        raise HTTPException(status_code=400, detail="upload a valid PNG file")
    out = _settings().data_dir / "jobs" / job_id / "images" / f"clause_{clause_index:03d}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(raw)
    store.add_artifact(
        job_id,
        PipelineStage.images,
        ArtifactType.image_png,
        out,
        meta={"source": "user_upload"},
    )
    log.info("clause_image_replaced_upload", job_id=job_id, clause=clause_index)
    return JSONResponse({"ok": True, "path": str(out)})


@app.post("/api/jobs/{job_id}/images/reset-all")
def reset_all_clause_images_endpoint(job_id: str, clear_raw: bool = True) -> JSONResponse:
    store = _store()
    j = store.get_job(job_id)
    if j is None:
        raise HTTPException(status_code=404, detail="job not found")
    _job_must_not_be_running_for_image_edit(j)
    img_dir = _settings().data_dir / "jobs" / job_id / "images"
    store.delete_all_image_png_artifacts(job_id)
    if img_dir.is_dir():
        for p in img_dir.glob("clause_*.png"):
            try:
                p.unlink()
            except OSError:
                pass
        if clear_raw:
            for sub in ("_flux_raw", "_grok_raw", "_api_raw"):
                d = img_dir / sub
                if d.is_dir():
                    for p in d.glob("*.png"):
                        try:
                            p.unlink()
                        except OSError:
                            pass
    store.update_job_progress(
        job_id,
        status=JobStatus.paused,
        clear_error=True,
        last_completed_stage=PipelineStage.plan,
        current_stage=PipelineStage.images,
    )
    log.info("all_clause_images_reset", job_id=job_id, clear_raw=clear_raw)
    return JSONResponse({"ok": True, "clear_raw": clear_raw})


@app.post("/api/jobs/{job_id}/pause-after-image")
def pause_after_image(job_id: str) -> JSONResponse:
    """Request cooperative pause after the current Comfy image step completes (images stage only)."""
    j = _store().get_job(job_id)
    if j is None:
        raise HTTPException(status_code=404, detail="job not found")
    if j.status != JobStatus.running or j.current_stage != PipelineStage.images:
        raise HTTPException(
            status_code=409,
            detail="pause-after-image is only available while the job is running on the images stage",
        )
    img_dir = _settings().data_dir / "jobs" / job_id / "images"
    img_dir.mkdir(parents=True, exist_ok=True)
    (img_dir / ".pause_after_image").write_text("1", encoding="utf-8")
    log.info("pause_after_image_requested", job_id=job_id)
    return JSONResponse({"ok": True, "job_id": job_id})


@app.post("/api/jobs/{job_id}/run")
def run_job(job_id: str, tasks: BackgroundTasks) -> JSONResponse:
    j = _store().get_job(job_id)
    if j is None:
        raise HTTPException(status_code=404, detail="job not found")
    if j.status == JobStatus.running:
        raise HTTPException(status_code=409, detail="job already running")
    if _store().has_blocking_pipeline_job(exclude_job_id=job_id):
        raise HTTPException(status_code=409, detail="another job is already running — wait for it to finish first")
    tasks.add_task(_run_pipeline, job_id)
    return JSONResponse({"ok": True, "job_id": job_id})


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> JSONResponse:
    j = _store().get_job(job_id)
    if j is None:
        raise HTTPException(status_code=404, detail="job not found")
    ok = _store().cancel_job(job_id)
    if not ok:
        raise HTTPException(status_code=409, detail="job is not running or pending")
    flag = _settings().data_dir / "jobs" / job_id / "images" / ".pause_after_image"
    flag.unlink(missing_ok=True)
    log.info("job_cancelled", job_id=job_id)
    return JSONResponse({"ok": True, "job_id": job_id})


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str) -> JSONResponse:
    j = _store().get_job(job_id)
    if j is None:
        raise HTTPException(status_code=404, detail="job not found")
    if j.status in ("running", "pending"):
        raise HTTPException(status_code=409, detail="cancel the job before deleting")
    _store().delete_job(job_id)
    log.info("job_deleted", job_id=job_id)
    return JSONResponse({"ok": True, "job_id": job_id})


@app.get("/api/jobs/{job_id}/logs")
def job_logs(job_id: str, limit: int = 200) -> list[dict]:
    return job_log_buffer.tail(job_id, limit=limit)


@app.get("/api/jobs/{job_id}/artifact/final.mp4")
def download_final(job_id: str) -> FileResponse:
    from shorts_pipeline.jobs.models import ArtifactType, PipelineStage
    art = _store().get_latest_artifact(job_id, PipelineStage.render, ArtifactType.final_mp4)
    if art is None:
        raise HTTPException(status_code=404, detail="final video not available")
    p = Path(art.path)
    if not p.is_file():
        raise HTTPException(status_code=404, detail="file missing on disk")
    return FileResponse(p, media_type="video/mp4", filename="final.mp4")


@app.get("/api/jobs/{job_id}/artifact/final_long.mp4")
def download_final_long(job_id: str) -> FileResponse:
    """Download the long version (0.9× speed, ~62–66 s) produced after the Shorts render."""
    from shorts_pipeline.jobs.models import ArtifactType, PipelineStage
    art = _store().get_latest_artifact(job_id, PipelineStage.render, ArtifactType.final_long_mp4)
    if art is None:
        raise HTTPException(status_code=404, detail="long version not available")
    p = Path(art.path)
    if not p.is_file():
        raise HTTPException(status_code=404, detail="file missing on disk")
    return FileResponse(p, media_type="video/mp4", filename="final_long.mp4")


# ── Script editor endpoints ───────────────────────────────────────────────────

@app.get("/api/jobs/{job_id}/script")
def get_script(job_id: str) -> JSONResponse:
    """Return the current plan's full_script, clause texts, and word count."""
    art = _store().get_latest_artifact(job_id, PipelineStage.plan, ArtifactType.plan_json)
    if art is None:
        raise HTTPException(status_code=404, detail="plan not found")
    p = Path(art.path)
    if not p.is_file():
        raise HTTPException(status_code=404, detail="plan file missing on disk")
    data = json.loads(p.read_text(encoding="utf-8"))
    clauses = data.get("clauses", [])
    full_script = data.get("full_script", "")
    return JSONResponse({
        "full_script": full_script,
        "word_count": len(full_script.split()),
        "historical_figure": data.get("historical_figure", ""),
        "clauses": [
            {"index": i, "text": c.get("text", "")}
            for i, c in enumerate(clauses)
        ],
    })


class ScriptUpdateBody(BaseModel):
    full_script: str
    clauses: list[dict] = []


@app.put("/api/jobs/{job_id}/script")
def update_script(job_id: str, body: ScriptUpdateBody) -> JSONResponse:
    """Manually replace script. Clears tts/align/render artifacts so pipeline re-runs from tts."""
    store = _store()
    j = store.get_job(job_id)
    if j is None:
        raise HTTPException(status_code=404, detail="job not found")
    if j.status == JobStatus.running:
        raise HTTPException(status_code=409, detail="job is running — pause or wait first")

    art = store.get_latest_artifact(job_id, PipelineStage.plan, ArtifactType.plan_json)
    if art is None:
        raise HTTPException(status_code=404, detail="plan not found")

    p = Path(art.path)
    data = json.loads(p.read_text(encoding="utf-8"))
    data["full_script"] = body.full_script.strip()
    for upd in body.clauses:
        idx = upd.get("index")
        txt = upd.get("text")
        if idx is not None and txt is not None and 0 <= idx < len(data.get("clauses", [])):
            data["clauses"][idx]["text"] = txt

    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    store.add_artifact(job_id, PipelineStage.plan, ArtifactType.plan_json, p,
                       meta={"manual_edit": True})

    store.delete_artifacts_from_stage(job_id, PipelineStage.tts)
    store.update_job_progress(
        job_id,
        status=JobStatus.pending,
        current_stage=PipelineStage.tts,
        last_completed_stage=PipelineStage.images,
        clear_error=True,
    )
    return JSONResponse({"ok": True, "word_count": len(body.full_script.split())})


class ScriptRegenerateBody(BaseModel):
    feedback: str = ""


@app.post("/api/jobs/{job_id}/script/regenerate")
def regenerate_script(job_id: str, body: ScriptRegenerateBody) -> JSONResponse:
    """Regenerate the plan with optional feedback, then re-run tts→align→render (keep images)."""
    store = _store()
    j = store.get_job(job_id)
    if j is None:
        raise HTTPException(status_code=404, detail="job not found")
    if store.has_running_job():
        raise HTTPException(status_code=409, detail="another job is already running")

    feedback = body.feedback.strip() or None

    def _do() -> None:
        try:
            s = effective_settings(_settings())
            orch = PipelineOrchestrator(s, store)
            orch.run_rescript(job_id, user_feedback=feedback)
            _run_pipeline(job_id)
        except Exception as exc:
            log.exception("rescript_pipeline_failed", job_id=job_id, error=str(exc)[:400])

    threading.Thread(target=_do, daemon=True).start()
    return JSONResponse({"ok": True, "status": "regenerating"})


@app.get("/api/analytics/overview")
def analytics_overview(jobs_limit: int = 300) -> dict:
    """Historical + aggregate duration analytics backed by SQLite ``stage_timings``."""
    if jobs_limit < 10 or jobs_limit > 3000:
        raise HTTPException(status_code=422, detail="jobs_limit must be 10–3000")
    return _store().get_analytics_overview(jobs_limit=jobs_limit)


# ── Publishing package endpoints ──────────────────────────────────────────────

def _load_publish_artifact(job_id: str) -> Path:
    from shorts_pipeline.jobs.models import ArtifactType, PipelineStage
    art = _store().get_latest_artifact(
        job_id, PipelineStage.publish, ArtifactType.publish_package_json
    )
    if art is None:
        raise HTTPException(status_code=404, detail="publish package not generated yet")
    p = Path(art.path)
    if not p.is_file():
        raise HTTPException(status_code=404, detail="publish package file missing on disk")
    return p


@app.get("/api/jobs/{job_id}/publish")
def get_publish_package(job_id: str) -> JSONResponse:
    """Return the parsed publishing-package JSON (titles, description, tags, thumbnail, ctr)."""
    import json as _json

    j = _store().get_job(job_id)
    if j is None:
        raise HTTPException(status_code=404, detail="job not found")
    p = _load_publish_artifact(job_id)
    try:
        data = _json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"corrupt publish package: {exc}")
    return JSONResponse(data)


@app.get("/api/jobs/{job_id}/publish/text")
def get_publish_package_text(job_id: str) -> FileResponse:
    """Download the paste-ready text sidecar (publish_package.txt)."""
    j = _store().get_job(job_id)
    if j is None:
        raise HTTPException(status_code=404, detail="job not found")
    json_path = _load_publish_artifact(job_id)
    txt_path = json_path.with_suffix(".txt")
    if not txt_path.is_file():
        raise HTTPException(status_code=404, detail="text sidecar missing on disk")
    return FileResponse(
        txt_path, media_type="text/plain; charset=utf-8", filename="publish_package.txt"
    )


@app.get("/api/jobs/{job_id}/artifact/publish_package.json")
def download_publish_package_json(job_id: str) -> FileResponse:
    """Download the raw publish_package.json file (full structured payload)."""
    j = _store().get_job(job_id)
    if j is None:
        raise HTTPException(status_code=404, detail="job not found")
    p = _load_publish_artifact(job_id)
    return FileResponse(
        p, media_type="application/json", filename="publish_package.json"
    )


def _regenerate_publish_package(job_id: str) -> None:
    """Background task body: re-run the publish stage out-of-band."""
    set_job_id(job_id)
    base = _settings()
    s = effective_settings(base)
    h = orchestrator_stage_handler(s, _store())
    h.run_publish(job_id)


@app.post("/api/jobs/{job_id}/publish/regenerate")
def regenerate_publish_package(
    job_id: str, tasks: BackgroundTasks
) -> JSONResponse:
    """Re-run only the publish stage. Useful for completed jobs that pre-date
    the publishing-package feature, or when the operator wants a fresh roll."""
    from shorts_pipeline.jobs.models import ArtifactType, PipelineStage

    j = _store().get_job(job_id)
    if j is None:
        raise HTTPException(status_code=404, detail="job not found")
    if j.status == JobStatus.running:
        raise HTTPException(
            status_code=409,
            detail="job is currently running — wait for it to finish before regenerating",
        )
    plan_art = _store().get_latest_artifact(
        job_id, PipelineStage.plan, ArtifactType.plan_json
    )
    if plan_art is None:
        raise HTTPException(
            status_code=409,
            detail="plan artifact missing — cannot generate publish package without it",
        )
    tasks.add_task(_regenerate_publish_package, job_id)
    log.info("publish_regenerate_requested", job_id=job_id)
    return JSONResponse({"ok": True, "job_id": job_id})


# ── File text extraction ──────────────────────────────────────────────────────

def _extract_text_from_pdf(data: bytes) -> str:
    import io
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    pages = []
    for page in reader.pages:
        t = page.extract_text()
        if t:
            pages.append(t)
    return "\n".join(pages)


def _extract_text_from_docx(data: bytes) -> str:
    import io
    from docx import Document
    doc = Document(io.BytesIO(data))
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())


def _extract_text(filename: str, data: bytes) -> str:
    name = filename.lower()
    if name.endswith(".pdf"):
        return _extract_text_from_pdf(data)
    if name.endswith(".docx"):
        return _extract_text_from_docx(data)
    # .txt / .md / .csv / plain text
    return data.decode("utf-8", errors="replace")


# ── Batch endpoints ───────────────────────────────────────────────────────────

@app.post("/api/batch/parse")
async def batch_parse(
    request: Request,
    file: UploadFile | None = File(default=None),
) -> dict:
    """
    Parse topics from an uploaded file (PDF / DOCX / TXT) OR a plain-text body.
    File upload takes priority if both are provided.
    """
    if file and file.filename:
        data = await file.read()
        try:
            text = _extract_text(file.filename, data)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"Could not read file: {exc}")
    else:
        raw = await request.body()
        text = raw.decode("utf-8", errors="replace")

    topics = parse_topics(text)
    if not topics:
        raise HTTPException(status_code=400, detail="No topics found in the provided text")
    return {"topics": topics, "count": len(topics)}


@app.post("/api/batch")
def batch_create(body: CreateBatchBody) -> dict:
    bgm = Path(body.bgm_path)
    if not bgm.is_file():
        raise HTTPException(status_code=400, detail=f"BGM path not found: {body.bgm_path}")
    topics = [t.strip() for t in body.topics if t.strip()]
    if not topics:
        raise HTTPException(status_code=400, detail="topics list is empty")
    b = _batch_store().create(body.name, topics, str(bgm.resolve()), topic_type=body.topic_type, language=body.language)
    log.info("batch_created", batch_id=b.id, total=len(topics))
    return _batch_to_dict(b)


@app.get("/api/batch/current")
def batch_current() -> dict | None:
    b = _batch_store().get_active()
    if b is None:
        return {}
    return _batch_to_dict_with_jobs(b)


@app.get("/api/batch/{batch_id}")
def batch_get(batch_id: str) -> dict:
    b = _batch_store().get(batch_id)
    if b is None:
        raise HTTPException(status_code=404, detail="batch not found")
    return _batch_to_dict_with_jobs(b)


@app.post("/api/batch/{batch_id}/start")
def batch_start(batch_id: str, body: StartBatchBody, tasks: BackgroundTasks) -> JSONResponse:
    bs = _batch_store()
    b = bs.get(batch_id)
    if b is None:
        raise HTTPException(status_code=404, detail="batch not found")
    if b.status == "completed":
        raise HTTPException(status_code=409, detail="batch already completed")

    from_idx = max(0, min(body.from_index, len(b.topics) - 1))
    # Clear stale job_ids from restart point so the failure-detection logic in
    # _tick_batch never sees an old failed job as the "previous" job.
    bs.clear_job_ids_from(batch_id, from_idx)
    bs.set_index_and_status(batch_id, from_idx, "running")

    # Kick off immediately if nothing is running
    if not _store().has_running_job():
        tasks.add_task(_start_next_batch_job, batch_id)

    log.info("batch_started", batch_id=batch_id, from_index=from_idx)
    return JSONResponse({"ok": True, "batch_id": batch_id, "from_index": from_idx})


@app.post("/api/batch/{batch_id}/pause")
def batch_pause(batch_id: str) -> JSONResponse:
    bs = _batch_store()
    b = bs.get(batch_id)
    if b is None:
        raise HTTPException(status_code=404, detail="batch not found")
    bs.set_status(batch_id, "paused")
    log.info("batch_paused", batch_id=batch_id)
    return JSONResponse({"ok": True, "batch_id": batch_id})


@app.delete("/api/batch/{batch_id}")
def batch_delete(batch_id: str) -> JSONResponse:
    _batch_store().delete(batch_id)
    return JSONResponse({"ok": True})


# ── Batch helpers ─────────────────────────────────────────────────────────────

def _start_next_batch_job(batch_id: str) -> None:
    """Synchronous helper: directly starts the next pending topic without the watcher."""
    bs = _batch_store()
    store = _store()

    b = bs.get(batch_id)
    if b is None or b.status != "running":
        return
    if store.has_running_job():
        return  # watcher will pick it up

    idx = b.current_index
    if idx >= len(b.topics):
        bs.set_status(batch_id, "completed")
        return

    topic = b.topics[idx]
    bgm = Path(b.bgm_path)
    if not bgm.is_file():
        bs.set_status(batch_id, "paused")
        return

    # Duplicate guard: skip if final.mp4 already exists, delete stale failed jobs otherwise
    existing_jid = _check_topic_duplicate(topic)
    if existing_jid:
        bs.record_job(batch_id, idx, existing_jid)
        bs.set_current_index(batch_id, idx + 1)
        log.info("batch_topic_skipped", batch_id=batch_id, topic=topic, index=idx, job_id=existing_jid)
        return  # watcher will pick up the next topic

    s = get_settings()
    cfg = JobConfigSnapshot(
        figure_name=topic,
        bgm_path=str(bgm.resolve()),
        topic_type=b.topic_type,
        language=b.language,
        watermark_enabled=s.watermark_enabled,
        end_plate_enabled=s.end_plate_enabled,
    )
    jid = store.create_job(cfg)
    bs.record_job(batch_id, idx, jid)
    bs.set_current_index(batch_id, idx + 1)
    threading.Thread(target=_run_pipeline, args=(jid,), daemon=True).start()
    # Returns immediately; _batch_watcher advances to the next topic after this job finishes.


def _batch_to_dict(b) -> dict:
    return {
        "id": b.id,
        "name": b.name,
        "status": b.status,
        "total": b.total,
        "current_index": b.current_index,
        "bgm_path": b.bgm_path,
        "topic_type": b.topic_type,
        "language": b.language,
        "created_at": b.created_at.isoformat(),
        "updated_at": b.updated_at.isoformat(),
    }


def _batch_to_dict_with_jobs(b) -> dict:
    store = _store()
    topics_out = []
    for i, (topic, jid) in enumerate(zip(b.topics, b.job_ids + [None] * len(b.topics))):
        job_status = None
        job_id = jid
        if jid:
            j = store.get_job(jid)
            job_status = j.status.value if j else None
        topics_out.append({
            "index": i,
            "name": topic,
            "job_id": job_id,
            "job_status": job_status,
        })
    d = _batch_to_dict(b)
    d["topics"] = topics_out
    return d


# ── Static UI ─────────────────────────────────────────────────────────────────

@app.get("/")
def index() -> HTMLResponse:
    static = Path(__file__).resolve().parent / "static" / "index.html"
    if not static.is_file():
        return HTMLResponse("<h1>shorts-pipeline</h1><p>Missing static UI.</p>", status_code=500)
    return HTMLResponse(static.read_text(encoding="utf-8"))
