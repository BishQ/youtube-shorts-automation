"""Scan job folders on disk and reconcile clause PNGs with SQLite artifacts."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from PIL import Image

from shorts_pipeline.config.settings import Settings
from shorts_pipeline.image_worker.triple_hybrid_image_generator import split_grok_flux_local_indices
from shorts_pipeline.jobs.models import ArtifactType, JobConfigSnapshot, PipelineStage
from shorts_pipeline.jobs.store import JobStore


def job_root(settings: Settings, job_id: str) -> Path:
    return (settings.data_dir / "jobs" / job_id).resolve()


def quick_png_ok(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size < 64:
        return False
    try:
        with Image.open(path) as im:
            im.verify()
        with Image.open(path) as im2:
            w, h = im2.size
    except Exception:
        return False
    return w >= 64 and h >= 64


def load_plan_clause_count(plan_path: Path) -> int | None:
    if not plan_path.is_file():
        return None
    try:
        data = json.loads(plan_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    clauses = data.get("clauses")
    if not isinstance(clauses, list):
        return None
    return len(clauses)


def scan_job_folder(settings: Settings, job_id: str) -> dict[str, Any]:
    """Read-only summary of plan + images on disk + DB artifact counts."""
    root = job_root(settings, job_id)
    plan_path = root / "plan.json"
    img_dir = root / "images"
    n_plan = load_plan_clause_count(plan_path)
    on_disk: list[int] = []
    if img_dir.is_dir() and n_plan is not None:
        for i in range(n_plan):
            p = img_dir / f"clause_{i:03d}.png"
            if quick_png_ok(p):
                on_disk.append(i)
    return {
        "job_id": job_id,
        "job_root": str(root),
        "plan_exists": plan_path.is_file(),
        "plan_clause_count": n_plan,
        "valid_clause_png_on_disk": len(on_disk),
        "valid_clause_indices_on_disk": on_disk,
        "missing_clause_indices": (
            [i for i in range(n_plan) if i not in set(on_disk)] if n_plan is not None else []
        ),
    }


def reconcile_image_artifacts_from_disk(store: JobStore, settings: Settings, job_id: str) -> dict[str, Any]:
    """Upsert artifact rows for each valid clause PNG found on disk. Does not delete rows."""
    root = job_root(settings, job_id)
    plan_path = root / "plan.json"
    n = load_plan_clause_count(plan_path)
    if n is None:
        raise ValueError("plan.json missing or invalid — cannot reconcile images")
    img_dir = root / "images"
    img_dir.mkdir(parents=True, exist_ok=True)
    registered = 0
    for i in range(n):
        p = img_dir / f"clause_{i:03d}.png"
        if quick_png_ok(p):
            store.add_artifact(
                job_id,
                PipelineStage.images,
                ArtifactType.image_png,
                p,
                meta={"source": "reconciled_from_disk"},
            )
            registered += 1
    arts = store.get_artifacts_for_stage(job_id, PipelineStage.images, ArtifactType.image_png)
    gate_ok = len(arts) == n and all(quick_png_ok(Path(a.path)) for a in arts)
    return {
        "registered_from_disk": registered,
        "artifact_rows_for_images": len(arts),
        "expected_clauses": n,
        "images_stage_complete": gate_ok,
    }


def clause_sidecar_paths(settings: Settings, job_id: str, clause_index: int, image_backend: str) -> list[Path]:
    """Paths to delete so the next images run fully regenerates this clause."""
    img_dir = job_root(settings, job_id) / "images"
    name = f"clause_{clause_index:03d}.png"
    paths: list[Path] = [img_dir / name]
    b = image_backend.lower().strip()
    if b == "hybrid_grok_flux":
        plan_path = job_root(settings, job_id) / "plan.json"
        n = load_plan_clause_count(plan_path)
        if n is not None:
            grok, flux, _ = split_grok_flux_local_indices(n, settings.grok_extra_clause_indices)
            if clause_index in flux:
                paths.append(img_dir / "_flux_raw" / name)
            if clause_index in grok:
                paths.append(img_dir / "_grok_raw" / name)
    return paths


def delete_clause_for_regen(
    store: JobStore,
    settings: Settings,
    job_id: str,
    clause_index: int,
    *,
    image_backend: str,
) -> dict[str, Any]:
    store.delete_image_artifact_for_clause(job_id, clause_index)
    removed_files: list[str] = []
    for p in clause_sidecar_paths(settings, job_id, clause_index, image_backend):
        if p.is_file():
            p.unlink()
            removed_files.append(str(p))
    return {"clause_index": clause_index, "removed_files": removed_files}


_JOB_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{6,80}$")


def resolve_folder_to_job_id(settings: Settings, folder_path: str) -> str:
    """Accept absolute job folder path or job id; return canonical job_id."""
    raw = folder_path.strip().strip('"').strip("'")
    p = Path(raw).expanduser()
    if not p.is_absolute():
        if _JOB_ID_RE.match(raw) and "/" not in raw and "\\" not in raw:
            p = (settings.data_dir / "jobs" / raw).resolve()
        else:
            p = (Path.cwd() / p).resolve()
    else:
        p = p.resolve()
    if p.name == "images":
        p = p.parent
    jobs_root = (settings.data_dir / "jobs").resolve()
    try:
        p.relative_to(jobs_root)
    except ValueError:
        raise ValueError(f"Folder must be under job data directory: {jobs_root}") from None
    job_id = p.name
    if not _JOB_ID_RE.match(job_id):
        raise ValueError("Folder name must look like a job id (e.g. jamukha-75f2535c)")
    if not p.is_dir():
        raise ValueError(f"Not a directory: {p}")
    return job_id


def read_import_bgm_hint(root: Path) -> str | None:
    """Optional ``import_meta.json`` in the job folder: ``{\"bgm_path\": \"C:/.../track.wav\"}``."""
    p = root / "import_meta.json"
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    bp = data.get("bgm_path")
    if not isinstance(bp, str):
        return None
    resolved = Path(bp).expanduser().resolve()
    if resolved.is_file():
        return str(resolved)
    return None


def default_bgm_path_for_reconcile(settings: Settings) -> str | None:
    """Resolve BGM for disk import: env ``default_bgm_path``, then ``<repo>/extra tools/bgm.(mp3|wav)``."""
    raw = (settings.default_bgm_path or "").strip()
    if raw:
        p = Path(raw).expanduser().resolve()
        if p.is_file():
            return str(p)
    try:
        # image_recovery.py → jobs → shorts_pipeline → src → project root
        root = Path(__file__).resolve().parents[3]
        for name in ("bgm.mp3", "bgm.wav", "bgm.ogg"):
            c = root / "extra tools" / name
            if c.is_file():
                return str(c.resolve())
    except (IndexError, OSError):
        pass
    return None


def build_job_config_for_disk_import(settings: Settings, job_id: str, bgm_path: str) -> JobConfigSnapshot:
    root = job_root(settings, job_id)
    plan_path = root / "plan.json"
    if not plan_path.is_file():
        raise ValueError("plan.json is required in the job folder to import this job")
    data = json.loads(plan_path.read_text(encoding="utf-8"))
    fig = data.get("historical_figure") or data.get("figure_name") or ""
    if isinstance(fig, str):
        fig = fig.strip()
    if not fig:
        fig = job_id.split("-")[0].replace("-", " ").strip().title() or "Imported"
    bgm = str(Path(bgm_path).expanduser().resolve())
    if not Path(bgm).is_file():
        raise ValueError(f"BGM path is not an existing file: {bgm}")
    return JobConfigSnapshot(
        figure_name=fig[:200],
        bgm_path=bgm,
        topic_type="historical_figure",
        language="en",
        watermark_enabled=False,
        end_plate_enabled=True,
        comfy_workflow_name=None,
        overlay_enabled=False,
    )


def ensure_plan_artifact_from_disk(store: JobStore, settings: Settings, job_id: str) -> bool:
    """If there is no plan artifact row but ``plan.json`` exists on disk, register it."""
    plan_p = job_root(settings, job_id) / "plan.json"
    if not plan_p.is_file():
        return False
    cur = store.get_latest_artifact(job_id, PipelineStage.plan, ArtifactType.plan_json)
    if cur is not None:
        return False
    store.add_artifact(
        job_id,
        PipelineStage.plan,
        ArtifactType.plan_json,
        plan_p,
        meta={"source": "imported_or_recovered_from_disk"},
    )
    return True
