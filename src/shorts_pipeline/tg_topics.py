"""Telegram forum topic routing: niche slug → topic thread ID."""

from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)


def load_topic_map(path: Path) -> tuple[int | None, dict[str, int]]:
    """Load ``config/tg_topics.json``.

    Returns ``(default_topic_id, {niche_slug: topic_id})``.
    Topic IDs of 0 or missing entries are ignored (niche falls back to default).
    """
    if not path.is_file():
        return None, {}

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("tg_topics_load_failed: %s — %s", path, exc)
        return None, {}

    default_raw = raw.get("_default_topic_id") or raw.get("default_topic_id")
    default_id: int | None = None
    if default_raw is not None:
        try:
            default_id = int(default_raw)
            if default_id <= 0:
                default_id = None
        except (TypeError, ValueError):
            default_id = None

    topics_raw = raw.get("topics") or {}
    if not isinstance(topics_raw, dict):
        return default_id, {}

    topic_map: dict[str, int] = {}
    for niche, topic_id in topics_raw.items():
        if niche.startswith("_"):
            continue
        try:
            tid = int(topic_id)
        except (TypeError, ValueError):
            continue
        if tid > 0:
            topic_map[str(niche).strip().lower()] = tid

    return default_id, topic_map


def resolve_topic_id(
    niche: str | None,
    *,
    default_topic_id: int | None,
    topic_map: dict[str, int],
) -> int | None:
    """Pick the forum topic ID for a job niche."""
    key = (niche or "").strip().lower()
    if key and key in topic_map:
        return topic_map[key]
    return default_topic_id


def read_job_niche(job_dir: Path) -> str | None:
    """Read niche from plan.json; returns normalized slug or None."""
    plan_path = job_dir / "plan.json"
    if not plan_path.is_file():
        return None
    try:
        data = json.loads(plan_path.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("tg_plan_parse_failed: %s — %s", plan_path, exc)
        return None
    niche = data.get("niche")
    if niche is None:
        return None
    text = str(niche).strip().lower()
    return text or None


def resolve_job_niche(
    job_dir: Path,
    *,
    plan_niche: str | None = None,
    default_niche: str = "documentary",
) -> str | None:
    """Niche for Telegram routing: plan field, else historical_figure -> default."""
    niche = (plan_niche or read_job_niche(job_dir) or "").strip().lower() or None
    if niche:
        return niche

    plan_path = job_dir / "plan.json"
    if plan_path.is_file():
        try:
            data = json.loads(plan_path.read_text(encoding="utf-8"))
            if str(data.get("historical_figure", "")).strip():
                fallback = (default_niche or "").strip().lower()
                return fallback or None
        except Exception as exc:
            log.warning("tg_plan_niche_parse_failed: %s — %s", plan_path, exc)
    return None
