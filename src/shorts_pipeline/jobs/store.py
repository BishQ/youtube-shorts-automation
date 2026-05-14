"""SQLite persistence for jobs and artifacts."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from shorts_pipeline.jobs.models import (
    ArtifactRow,
    ArtifactType,
    JobConfigSnapshot,
    JobErrorDetail,
    JobRecord,
    JobStatus,
    PipelineStage,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _dt_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


class JobStore:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self.connect() as c:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    figure_name TEXT NOT NULL,
                    topic_type TEXT NOT NULL DEFAULT 'historical_figure',
                    language TEXT NOT NULL DEFAULT 'en',
                    status TEXT NOT NULL,
                    current_stage TEXT,
                    last_completed_stage TEXT,
                    error_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    config_snapshot_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS artifacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                    stage TEXT NOT NULL,
                    artifact_type TEXT NOT NULL,
                    path TEXT NOT NULL,
                    sha256 TEXT,
                    meta_json TEXT,
                    completed_at TEXT NOT NULL,
                    UNIQUE(job_id, stage, artifact_type, path)
                );

                CREATE INDEX IF NOT EXISTS idx_artifacts_job ON artifacts (job_id);

                CREATE TABLE IF NOT EXISTS stage_timings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL REFERENCES jobs (id) ON DELETE CASCADE,
                    pipeline_stage TEXT NOT NULL,
                    display_label TEXT NOT NULL,
                    skipped INTEGER NOT NULL DEFAULT 0,
                    started_at TEXT NOT NULL,
                    ended_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_stage_timings_job ON stage_timings (job_id);
                CREATE INDEX IF NOT EXISTS idx_stage_timings_started ON stage_timings (started_at);
                """
            )

    def has_blocking_pipeline_job(self, *, exclude_job_id: str | None = None) -> bool:
        """True if any job is running or cooperatively paused (optional exclude for resume)."""
        with self.connect() as c:
            if exclude_job_id:
                row = c.execute(
                    "SELECT id FROM jobs WHERE status IN ('running', 'paused') AND id != ? LIMIT 1",
                    (exclude_job_id,),
                ).fetchone()
            else:
                row = c.execute(
                    "SELECT id FROM jobs WHERE status IN ('running', 'paused') LIMIT 1",
                ).fetchone()
        return row is not None

    def has_running_job(self) -> bool:
        """True when a job holds the single pipeline slot (running or cooperatively paused)."""
        return self.has_blocking_pipeline_job()

    def create_job(self, config: JobConfigSnapshot) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", config.figure_name.lower()).strip("-")[:32]
        short = uuid.uuid4().hex[:8]
        jid = f"{slug}-{short}"
        now = _utc_now()
        with self.connect() as c:
            c.execute(
                """
                INSERT INTO jobs (
                  id, figure_name, topic_type, language, status,
                  current_stage, last_completed_stage, error_json,
                  created_at, updated_at, config_snapshot_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    jid,
                    config.figure_name,
                    config.topic_type,
                    config.language,
                    JobStatus.pending.value,
                    PipelineStage.plan.value,
                    None,
                    None,
                    _dt_iso(now),
                    _dt_iso(now),
                    config.model_dump_json(),
                ),
            )
        return jid

    def import_job_if_missing(self, job_id: str, config: JobConfigSnapshot) -> bool:
        """Insert a job row with a fixed id (folder name). No-op if the job already exists."""
        if self.get_job(job_id) is not None:
            return False
        now = _utc_now()
        with self.connect() as c:
            c.execute(
                """
                INSERT INTO jobs (
                  id, figure_name, topic_type, language, status,
                  current_stage, last_completed_stage, error_json,
                  created_at, updated_at, config_snapshot_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    config.figure_name,
                    config.topic_type,
                    config.language,
                    JobStatus.paused.value,
                    PipelineStage.images.value,
                    PipelineStage.plan.value,
                    None,
                    _dt_iso(now),
                    _dt_iso(now),
                    config.model_dump_json(),
                ),
            )
        return True

    def get_job(self, job_id: str) -> JobRecord | None:
        with self.connect() as c:
            row = c.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    def _row_to_record(self, row: sqlite3.Row) -> JobRecord:
        err = None
        if row["error_json"]:
            err = JobErrorDetail.model_validate_json(row["error_json"])
        cfg = JobConfigSnapshot.model_validate_json(row["config_snapshot_json"])
        return JobRecord(
            id=row["id"],
            figure_name=row["figure_name"],
            topic_type=row["topic_type"],
            language=row["language"],
            status=JobStatus(row["status"]),
            current_stage=PipelineStage(row["current_stage"]) if row["current_stage"] else None,
            last_completed_stage=PipelineStage(row["last_completed_stage"])
            if row["last_completed_stage"]
            else None,
            error=err,
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
            config_snapshot=cfg,
        )

    def update_job_progress(
        self,
        job_id: str,
        *,
        status: JobStatus | None = None,
        current_stage: PipelineStage | None = None,
        clear_current_stage: bool = False,
        last_completed_stage: PipelineStage | None = None,
        error: JobErrorDetail | None = None,
        clear_error: bool = False,
    ) -> None:
        fields: list[str] = []
        values: list[Any] = []
        if status is not None:
            fields.append("status = ?")
            values.append(status.value)
        if clear_current_stage:
            fields.append("current_stage = NULL")
        elif current_stage is not None:
            fields.append("current_stage = ?")
            values.append(current_stage.value)
        if last_completed_stage is not None:
            fields.append("last_completed_stage = ?")
            values.append(last_completed_stage.value)
        if clear_error:
            fields.append("error_json = NULL")
        elif error is not None:
            fields.append("error_json = ?")
            values.append(error.model_dump_json())
        fields.append("updated_at = ?")
        values.append(_dt_iso(_utc_now()))
        values.append(job_id)
        with self.connect() as c:
            c.execute(f"UPDATE jobs SET {', '.join(fields)} WHERE id = ?", values)

    def cancel_job(self, job_id: str) -> bool:
        """Mark a pending/running job as failed with a cancelled message. Returns False if not found."""
        with self.connect() as c:
            cur = c.execute(
                "SELECT status FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if cur is None:
                return False
            if cur["status"] not in ("pending", "running", "paused"):
                return False
            error = JobErrorDetail(stage="cancelled", code="Cancelled", message="Cancelled by user", detail=None)
            c.execute(
                "UPDATE jobs SET status = ?, error_json = ?, updated_at = ? WHERE id = ?",
                (JobStatus.failed.value, error.model_dump_json(), _dt_iso(_utc_now()), job_id),
            )
        self.close_open_stage_timings(job_id)
        return True

    def delete_job(self, job_id: str) -> None:
        with self.connect() as c:
            c.execute("DELETE FROM jobs WHERE id = ?", (job_id,))

    def find_jobs_by_figure(self, figure_name: str) -> list[JobRecord]:
        """Return all jobs whose figure_name matches (case-insensitive), newest first."""
        with self.connect() as c:
            rows = c.execute(
                "SELECT * FROM jobs WHERE lower(figure_name) = lower(?) ORDER BY created_at DESC",
                (figure_name,),
            ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def list_jobs(self, limit: int = 100) -> list[JobRecord]:
        with self.connect() as c:
            rows = c.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def add_artifact(
        self,
        job_id: str,
        stage: PipelineStage,
        artifact_type: ArtifactType,
        path: Path,
        *,
        meta: dict[str, Any] | None = None,
    ) -> ArtifactRow:
        path = path.resolve()
        sha = file_sha256(path) if path.is_file() else None
        now = _utc_now()
        meta_json = json.dumps(meta) if meta is not None else None
        with self.connect() as c:
            cur = c.execute(
                """
                INSERT INTO artifacts (job_id, stage, artifact_type, path, sha256, meta_json, completed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id, stage, artifact_type, path) DO UPDATE SET
                  sha256 = excluded.sha256,
                  meta_json = excluded.meta_json,
                  completed_at = excluded.completed_at
                RETURNING id
                """,
                (
                    job_id,
                    stage.value,
                    artifact_type.value,
                    str(path),
                    sha,
                    meta_json,
                    _dt_iso(now),
                ),
            )
            aid = int(cur.fetchone()[0])
        return ArtifactRow(
            id=aid,
            job_id=job_id,
            stage=stage,
            artifact_type=artifact_type,
            path=str(path),
            sha256=sha,
            meta_json=meta_json,
            completed_at=now,
        )

    def delete_image_artifact_for_clause(self, job_id: str, clause_index: int) -> int:
        suffix = f"clause_{clause_index:03d}.png"
        with self.connect() as c:
            cur = c.execute(
                """
                DELETE FROM artifacts
                WHERE job_id = ? AND stage = ? AND artifact_type = ? AND path LIKE ?
                """,
                (
                    job_id,
                    PipelineStage.images.value,
                    ArtifactType.image_png.value,
                    f"%{suffix}",
                ),
            )
            return int(cur.rowcount or 0)

    def delete_all_image_png_artifacts(self, job_id: str) -> int:
        with self.connect() as c:
            cur = c.execute(
                """
                DELETE FROM artifacts
                WHERE job_id = ? AND stage = ? AND artifact_type = ?
                """,
                (job_id, PipelineStage.images.value, ArtifactType.image_png.value),
            )
            return int(cur.rowcount or 0)

    def delete_artifacts_from_stage(self, job_id: str, from_stage: PipelineStage) -> int:
        """Delete all artifact rows for *from_stage* and every stage after it.

        Used when re-scripting a job: keeps plan + images, clears tts/align/render/publish
        so that ``resume_job`` re-runs those stages with the updated script.
        """
        stage_order = list(PipelineStage)
        from_idx = stage_order.index(from_stage)
        stages = [s.value for s in stage_order[from_idx:]]
        placeholders = ",".join("?" * len(stages))
        with self.connect() as c:
            cur = c.execute(
                f"DELETE FROM artifacts WHERE job_id = ? AND stage IN ({placeholders})",
                [job_id, *stages],
            )
            return int(cur.rowcount or 0)

    def merge_image_meta_for_clause(self, job_id: str, clause_index: int, patch: dict[str, Any]) -> bool:
        suffix = f"clause_{clause_index:03d}.png"
        with self.connect() as c:
            row = c.execute(
                """
                SELECT id, meta_json FROM artifacts
                WHERE job_id = ? AND stage = ? AND artifact_type = ? AND path LIKE ?
                LIMIT 1
                """,
                (
                    job_id,
                    PipelineStage.images.value,
                    ArtifactType.image_png.value,
                    f"%{suffix}",
                ),
            ).fetchone()
            if row is None:
                return False
            old: dict[str, Any] = {}
            if row["meta_json"]:
                try:
                    parsed = json.loads(row["meta_json"])
                    if isinstance(parsed, dict):
                        old = parsed
                except Exception:
                    pass
            old.update(patch)
            c.execute(
                "UPDATE artifacts SET meta_json = ? WHERE id = ?",
                (json.dumps(old), int(row["id"])),
            )
        return True

    def get_artifacts_for_stage(
        self, job_id: str, stage: PipelineStage, artifact_type: ArtifactType | None = None
    ) -> list[ArtifactRow]:
        q = "SELECT * FROM artifacts WHERE job_id = ? AND stage = ?"
        params: list[Any] = [job_id, stage.value]
        if artifact_type is not None:
            q += " AND artifact_type = ?"
            params.append(artifact_type.value)
        q += " ORDER BY id"
        with self.connect() as c:
            rows = c.execute(q, params).fetchall()
        return [self._artifact_row(r) for r in rows]

    def get_latest_artifact(
        self, job_id: str, stage: PipelineStage, artifact_type: ArtifactType
    ) -> ArtifactRow | None:
        rows = self.get_artifacts_for_stage(job_id, stage, artifact_type)
        return rows[-1] if rows else None

    def _artifact_row(self, row: sqlite3.Row) -> ArtifactRow:
        return ArtifactRow(
            id=row["id"],
            job_id=row["job_id"],
            stage=PipelineStage(row["stage"]),
            artifact_type=ArtifactType(row["artifact_type"]),
            path=row["path"],
            sha256=row["sha256"],
            meta_json=row["meta_json"],
            completed_at=_parse_dt(row["completed_at"]),
        )

    # ── Stage duration analytics (pipeline wall-clock attribution) ─────────────

    def close_open_stage_timings(self, job_id: str) -> int:
        """Seal any orphaned open rows (crash / cancel before ``record_stage_end``)."""
        now = _dt_iso(_utc_now())
        with self.connect() as c:
            cur = c.execute(
                """
                UPDATE stage_timings SET ended_at = ?
                WHERE job_id = ? AND ended_at IS NULL AND skipped = 0
                """,
                (now, job_id),
            )
            return cur.rowcount

    def record_stage_skip(
        self, job_id: str, stage: PipelineStage, display_label: str
    ) -> None:
        instant = _dt_iso(_utc_now())
        with self.connect() as c:
            c.execute(
                """
                INSERT INTO stage_timings
                  (job_id, pipeline_stage, display_label, skipped, started_at, ended_at)
                VALUES (?, ?, ?, 1, ?, ?)
                """,
                (job_id, stage.value, display_label, instant, instant),
            )

    def record_stage_start(
        self, job_id: str, stage: PipelineStage, display_label: str
    ) -> None:
        """Open a tracked segment. Closing any dangling row avoids resume overlap."""
        self.close_open_stage_timings(job_id)
        now = _dt_iso(_utc_now())
        with self.connect() as c:
            c.execute(
                """
                INSERT INTO stage_timings
                  (job_id, pipeline_stage, display_label, skipped, started_at, ended_at)
                VALUES (?, ?, ?, 0, ?, NULL)
                """,
                (job_id, stage.value, display_label, now),
            )

    def record_stage_end(self, job_id: str) -> None:
        """Seal the newest open timing row for *job_id*."""
        now = _dt_iso(_utc_now())
        with self.connect() as c:
            c.execute(
                """
                UPDATE stage_timings SET ended_at = ?
                WHERE id = (
                  SELECT id FROM stage_timings
                  WHERE job_id = ? AND ended_at IS NULL AND skipped = 0
                  ORDER BY id DESC LIMIT 1
                )
                """,
                (now, job_id),
            )

    def get_stage_timings_for_job(self, job_id: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        now = _utc_now()
        with self.connect() as c:
            q = c.execute(
                """
                SELECT id, pipeline_stage, display_label, skipped, started_at, ended_at
                FROM stage_timings
                WHERE job_id = ?
                ORDER BY id ASC
                """,
                (job_id,),
            ).fetchall()
        for r in q:
            started = _parse_dt(r["started_at"])
            ended = _parse_dt(r["ended_at"]) if r["ended_at"] else None
            skipped = bool(r["skipped"])
            duration_s = 0.0 if skipped else (ended - started).total_seconds() if ended else (now - started).total_seconds()
            rows.append(
                {
                    "id": int(r["id"]),
                    "stage": str(r["pipeline_stage"]),
                    "label": str(r["display_label"]),
                    "skipped": skipped,
                    "started_at": str(r["started_at"]),
                    "ended_at": r["ended_at"],
                    "duration_seconds": round(float(duration_s), 2),
                    "active": (not skipped) and (r["ended_at"] is None),
                }
            )
        return rows

    def job_total_tracked_seconds(self, job_id: str) -> float:
        ts = self.get_stage_timings_for_job(job_id)
        return round(sum(float(t["duration_seconds"]) for t in ts), 2)

    def get_analytics_overview(self, *, jobs_limit: int = 250) -> dict[str, Any]:
        """Aggregate dashboard payloads from ``stage_timings`` + jobs table."""
        from collections import defaultdict

        from shorts_pipeline.jobs.stage_timing import STAGE_TIMING_LABELS, ordered_stage_keys

        now = _utc_now()

        jobs = self.list_jobs(limit=jobs_limit)
        job_lookup = {j.id: j for j in jobs}

        segments: list[sqlite3.Row] = []
        with self.connect() as c:
            segments = list(
                c.execute(
                    """
                    SELECT job_id, pipeline_stage, skipped, started_at, ended_at
                    FROM stage_timings
                    ORDER BY started_at DESC
                    """
                ).fetchall()
            )

        def seg_duration_started(r: sqlite3.Row) -> tuple[float, datetime]:
            st = _parse_dt(r["started_at"])
            if r["skipped"]:
                return 0.0, st
            if r["ended_at"]:
                et = _parse_dt(r["ended_at"])
                return max(0.0, (et - st).total_seconds()), st
            return max(0.0, (now - st).total_seconds()), st

        totals_by_job: dict[str, float] = defaultdict(float)
        per_job_stage: dict[str, dict[str, float]] = defaultdict(
            lambda: defaultdict(float)
        )
        stage_buckets: dict[str, list[float]] = defaultdict(list)
        daily_hours: defaultdict[str, float] = defaultdict(float)
        hour_matrix: defaultdict[tuple[int, int], float] = defaultdict(float)
        now_date = now.date()

        for r in segments:
            dur_s, started = seg_duration_started(r)
            jid = str(r["job_id"])
            totals_by_job[jid] += dur_s
            p_st = str(r["pipeline_stage"])
            if not r["skipped"]:
                stage_buckets[p_st].append(dur_s)
                per_job_stage[jid][p_st] += dur_s
            ds = started.date().isoformat()
            daily_hours[ds] += dur_s / 3600.0
            dow = started.weekday()
            hod = started.hour
            hour_matrix[(dow, hod)] += dur_s / 3600.0

        grand_total = round(sum(totals_by_job.values()), 2)

        per_stage: list[dict[str, Any]] = []
        for key in ordered_stage_keys():
            arr = stage_buckets.get(key, [])
            per_stage.append(
                {
                    "stage": key,
                    "label": STAGE_TIMING_LABELS.get(key, key),
                    "sessions": len(arr),
                    "avg_seconds": round(sum(arr) / len(arr), 1) if arr else 0,
                    "sum_seconds": round(sum(arr), 1),
                }
            )

        jobs_ranked: list[dict[str, Any]] = []
        for jid, secs in sorted(
            totals_by_job.items(), key=lambda kv: kv[1], reverse=True
        ):
            jr = job_lookup.get(jid)
            jobs_ranked.append(
                {
                    "job_id": jid,
                    "figure_name": jr.figure_name if jr else jid,
                    "status": jr.status.value if jr else "unknown",
                    "created_at": jr.created_at.isoformat() if jr else "",
                    "total_seconds": round(float(secs), 2),
                    "per_stage_seconds": {
                        k: round(v, 2) for k, v in dict(per_job_stage[jid]).items()
                    },
                }
            )

        timeline_days = [
            (now_date - timedelta(days=29 - i)).isoformat() for i in range(30)
        ]

        daily_totals = [
            {
                "date": d,
                "hours_active": round(daily_hours.get(d, 0.0), 2),
                "seconds": round(daily_hours.get(d, 0.0) * 3600.0, 2),
            }
            for d in timeline_days
        ]

        dow_labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        heatmap_hours = [{"dow": d, "dow_label": dow_labels[d], "hour": h, "hours_engaged": round(hour_matrix.get((d, h), 0.0), 2)} for d in range(7) for h in range(24)]

        return {
            "generated_at": _dt_iso(now),
            "distinct_jobs_timed": len(totals_by_job),
            "segment_rows": len(segments),
            "total_tracked_seconds": grand_total,
            "total_tracked_human": self._humanize_seconds(grand_total),
            "max_job_seconds": round(float(max(totals_by_job.values(), default=0.0)), 2),
            "per_stage": per_stage,
            "jobs_ranked": jobs_ranked[: min(80, len(jobs_ranked))],
            "daily_totals_last_30d": daily_totals,
            "week_hour_heatmap": heatmap_hours,
        }


    @staticmethod
    def _humanize_seconds(total_seconds: float) -> str:
        if total_seconds < 60:
            return f"{int(total_seconds)}s"
        secs = int(round(total_seconds))
        m, sec = divmod(secs, 60)
        h, mn = divmod(m, 60)
        parts: list[str] = []
        if h:
            parts.append(f"{h}h")
        if mn:
            parts.append(f"{mn}m")
        if sec and not h:
            parts.append(f"{sec}s")
        return " ".join(parts) if parts else "0s"


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_artifact_path(path: Path, expected_sha: str | None) -> bool:
    if not path.is_file():
        return False
    if expected_sha is None:
        return path.stat().st_size > 0
    return file_sha256(path) == expected_sha
