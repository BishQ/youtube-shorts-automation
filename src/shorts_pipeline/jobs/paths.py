"""Filesystem paths for jobs (supports legacy + niche-based layouts).

Legacy layout (older jobs):
  data/jobs/<job_id>/

New layout (niche-first, for organization):
  data/jobs/<niche>/<job_id>/

This module is the single source of truth for resolving job directories.
"""

from __future__ import annotations

import re
from pathlib import Path

from shorts_pipeline.config.settings import Settings
from shorts_pipeline.jobs.store import JobStore


_SAFE_SEGMENT_RE = re.compile(r"[^a-z0-9_\-]+")


def _safe_segment(value: str, *, default: str) -> str:
    s = (value or "").strip().lower()
    s = _SAFE_SEGMENT_RE.sub("-", s).strip("-")
    return s or default


def legacy_job_dir(settings: Settings, job_id: str) -> Path:
    return (settings.data_dir / "jobs" / job_id).resolve()


def niche_folder_name(topic_type: str) -> str:
    """Map internal topic_type to a stable on-disk folder name.

    The planner uses `topic_type="historical_figure"` for documentary runs.
    On disk, we store those under `documentary/` for readability.
    """
    t = (topic_type or "").strip().lower()
    if t == "historical_figure":
        return "documentary"
    return t or "unknown"


def niche_job_dir(settings: Settings, niche: str, job_id: str) -> Path:
    safe = _safe_segment(niche_folder_name(niche), default="unknown")
    return (settings.data_dir / "jobs" / safe / job_id).resolve()


def resolve_job_dir(settings: Settings, store: JobStore, job_id: str) -> Path:
    """Resolve job directory for reads/writes, preserving backward compatibility."""
    legacy = legacy_job_dir(settings, job_id)
    if legacy.is_dir():
        return legacy

    rec = store.get_job(job_id)
    niche = rec.topic_type if rec is not None else "unknown"
    if rec is not None:
        niche = rec.config_snapshot.planner_niche()
    return niche_job_dir(settings, niche, job_id)


def resolve_job_dir_for_folder_scan(settings: Settings, folder: Path) -> str:
    """Given a path under data/jobs, return the job_id (supports niche subfolders)."""
    p = folder.resolve()
    jobs_root = (settings.data_dir / "jobs").resolve()
    p.relative_to(jobs_root)  # raises if not under jobs
    # data/jobs/<job_id> OR data/jobs/<niche>/<job_id>
    if p.name == "images":
        p = p.parent
    return p.name

