"""Batch queue: ordered list of topics processed one by one."""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class BatchRecord:
    id: str
    name: str
    topics: list[str]
    job_ids: list[str | None]   # parallel to topics; None = not yet created
    current_index: int          # index of NEXT topic to process
    status: str                 # idle | running | paused | completed
    bgm_path: str
    topic_type: str
    language: str
    created_at: datetime
    updated_at: datetime

    @property
    def done_count(self) -> int:
        return sum(1 for jid in self.job_ids if jid is not None and
                   not (jid.startswith("__fail_") or jid.startswith("__skip_")))

    @property
    def total(self) -> int:
        return len(self.topics)

    @property
    def is_finished(self) -> bool:
        return self.current_index >= self.total and self.status != "running"


class BatchStore:
    def __init__(self, db_path: Path) -> None:
        self._db = db_path
        self._db.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        c = sqlite3.connect(self._db, timeout=30)
        c.row_factory = sqlite3.Row
        try:
            c.execute("PRAGMA foreign_keys = ON")
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()

    def _init(self) -> None:
        with self._conn() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS batches (
                    id          TEXT PRIMARY KEY,
                    name        TEXT NOT NULL,
                    topics_json TEXT NOT NULL,
                    job_ids_json TEXT NOT NULL DEFAULT '[]',
                    current_index INTEGER NOT NULL DEFAULT 0,
                    status      TEXT NOT NULL DEFAULT 'idle',
                    bgm_path    TEXT NOT NULL DEFAULT '',
                    topic_type  TEXT NOT NULL DEFAULT 'historical_figure',
                    language    TEXT NOT NULL DEFAULT 'en',
                    created_at  TEXT NOT NULL,
                    updated_at  TEXT NOT NULL
                )
            """)
            # Migrate tables created before topic_type/language were added
            for col, default in [
                ("topic_type", "'historical_figure'"),
                ("language", "'en'"),
            ]:
                try:
                    c.execute(
                        f"ALTER TABLE batches ADD COLUMN {col} TEXT NOT NULL DEFAULT {default}"
                    )
                except sqlite3.OperationalError:
                    pass  # column already exists

    # ── CRUD ──────────────────────────────────────────────────────────────────

    def create(
        self,
        name: str,
        topics: list[str],
        bgm_path: str,
        *,
        topic_type: str = "historical_figure",
        language: str = "en",
    ) -> BatchRecord:
        bid = uuid.uuid4().hex[:12]
        now = _now_iso()
        null_ids: list[None] = [None] * len(topics)
        with self._conn() as c:
            c.execute(
                """INSERT INTO batches
                   (id, name, topics_json, job_ids_json, current_index, status, bgm_path,
                    topic_type, language, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (bid, name, json.dumps(topics), json.dumps(null_ids),
                 0, "idle", bgm_path, topic_type, language, now, now),
            )
        return self.get(bid)  # type: ignore[return-value]

    def get(self, bid: str) -> BatchRecord | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM batches WHERE id=?", (bid,)).fetchone()
        return self._to_record(row) if row else None

    def get_active(self) -> BatchRecord | None:
        """Most recent batch that is running or paused (not yet completed/deleted)."""
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM batches WHERE status IN ('running','paused','idle') "
                "ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        return self._to_record(row) if row else None

    def list_all(self, limit: int = 20) -> list[BatchRecord]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM batches ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._to_record(r) for r in rows]

    # ── Mutations ─────────────────────────────────────────────────────────────

    def set_status(self, bid: str, status: str) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE batches SET status=?, updated_at=? WHERE id=?",
                (status, _now_iso(), bid),
            )

    def set_current_index(self, bid: str, idx: int) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE batches SET current_index=?, updated_at=? WHERE id=?",
                (idx, _now_iso(), bid),
            )

    def set_index_and_status(self, bid: str, idx: int, status: str) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE batches SET current_index=?, status=?, updated_at=? WHERE id=?",
                (idx, status, _now_iso(), bid),
            )

    def record_job(self, bid: str, topic_idx: int, job_id: str) -> None:
        with self._conn() as c:
            row = c.execute(
                "SELECT job_ids_json FROM batches WHERE id=?", (bid,)
            ).fetchone()
            if row is None:
                return
            ids: list[str | None] = json.loads(row["job_ids_json"])
            while len(ids) <= topic_idx:
                ids.append(None)
            ids[topic_idx] = job_id
            c.execute(
                "UPDATE batches SET job_ids_json=?, updated_at=? WHERE id=?",
                (json.dumps(ids), _now_iso(), bid),
            )

    def clear_job_ids_from(self, bid: str, from_index: int) -> None:
        """Null-out job_ids at and after from_index (used when restarting a batch mid-way)."""
        with self._conn() as c:
            row = c.execute(
                "SELECT job_ids_json FROM batches WHERE id=?", (bid,)
            ).fetchone()
            if row is None:
                return
            ids: list[str | None] = json.loads(row["job_ids_json"])
            for i in range(from_index, len(ids)):
                ids[i] = None
            c.execute(
                "UPDATE batches SET job_ids_json=?, updated_at=? WHERE id=?",
                (json.dumps(ids), _now_iso(), bid),
            )

    def delete(self, bid: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM batches WHERE id=?", (bid,))

    # ── Internal ──────────────────────────────────────────────────────────────

    def _to_record(self, row: sqlite3.Row) -> BatchRecord:
        keys = row.keys()
        return BatchRecord(
            id=row["id"],
            name=row["name"],
            topics=json.loads(row["topics_json"]),
            job_ids=json.loads(row["job_ids_json"]),
            current_index=row["current_index"],
            status=row["status"],
            bgm_path=row["bgm_path"],
            topic_type=row["topic_type"] if "topic_type" in keys else "historical_figure",
            language=row["language"] if "language" in keys else "en",
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )


# ── Topic list parser ─────────────────────────────────────────────────────────

# Matches "1. Topic", "1) Topic", "1: Topic", "- Topic", "* Topic", "• Topic"
_LIST_PREFIX  = re.compile(r"^\s*(?:\d+[\.\):\-]\s+|[-*•]\s+)")
_NUMBERED_LINE = re.compile(r"^\s*\d+[\.\):\-]\s+")


def parse_topics(text: str) -> list[str]:
    """
    Parse an ordered or plain list of topics from text.

    Strategy:
    - If the text has ANY numbered lines (1. X, 2. X …), extract ONLY numbered
      lines — this skips title headers, footers, and stray plain text.
    - Otherwise fall back to every non-empty line as a topic.
    """
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    numbered = [l for l in lines if _NUMBERED_LINE.match(l)]

    if numbered:
        # Numbered doc — only take numbered items
        source = numbered
    else:
        # Plain list — take every non-empty line
        source = lines

    out: list[str] = []
    for line in source:
        m = _LIST_PREFIX.match(line)
        topic = line[m.end():].strip() if m else line
        if topic and not topic.isdigit() and len(topic) >= 2:
            out.append(topic)
    return out
