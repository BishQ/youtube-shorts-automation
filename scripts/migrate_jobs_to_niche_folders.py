"""Move legacy jobs into data/jobs/<niche>/<job_id> and rewrite sqlite artifact paths.

Safe usage:
  - Default: migrate only completed/failed jobs.
  - Optionally migrate paused jobs too (safe as long as NOT running/pending).
  - Updates artifacts.path in data/jobs.sqlite so resume/download keeps working.

This is optional: the pipeline can read legacy folders forever, but migrating keeps the
filesystem clean.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

# Repo root on path (match other scripts)
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=50000, help="max jobs to consider (newest-first)")
    ap.add_argument("--dry-run", action="store_true", help="print actions without moving/updating")
    ap.add_argument(
        "--include-paused",
        action="store_true",
        help="also migrate paused jobs (skips running/pending).",
    )
    ap.add_argument(
        "--verbose",
        action="store_true",
        help="print skip reasons for the first ~50 skipped jobs.",
    )
    args = ap.parse_args()

    from shorts_pipeline.config.settings import Settings
    from shorts_pipeline.jobs.paths import legacy_job_dir, niche_folder_name, niche_job_dir
    from shorts_pipeline.jobs.store import JobStore

    s = Settings()
    db = JobStore(s.data_dir / "jobs.sqlite")

    movable_statuses = {"completed", "failed"}
    if args.include_paused:
        movable_statuses.add("paused")
    touched = 0
    skipped = 0
    skipped_printed = 0

    for rec in db.list_jobs(limit=args.limit):
        status = getattr(rec.status, "value", str(rec.status))
        if status not in movable_statuses:
            skipped += 1
            if args.verbose and skipped_printed < 50:
                print(f"[skip] {rec.id} status={status}")
                skipped_printed += 1
            continue

        old_dir = legacy_job_dir(s, rec.id)
        if not old_dir.is_dir():
            # Already migrated or missing on disk
            skipped += 1
            if args.verbose and skipped_printed < 50:
                print(f"[skip] {rec.id} no_legacy_dir")
                skipped_printed += 1
            continue

        new_dir = niche_job_dir(s, niche_folder_name(rec.topic_type), rec.id)
        if new_dir.is_dir():
            # Both exist -> do nothing (manual state)
            skipped += 1
            if args.verbose and skipped_printed < 50:
                print(f"[skip] {rec.id} new_dir_exists")
                skipped_printed += 1
            continue

        old_prefix = str(old_dir.resolve())
        new_prefix = str(new_dir.resolve())

        if args.dry_run:
            print(f"[dry-run] move {old_dir} -> {new_dir}")
            print(f"[dry-run] rewrite artifacts.path prefix {old_prefix} -> {new_prefix}")
            touched += 1
            continue

        new_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(old_dir), str(new_dir))

        # Rewrite artifacts paths for this job. Paths are stored as absolute strings.
        like = old_prefix + "%"
        with db.connect() as c:
            c.execute(
                """
                UPDATE artifacts
                   SET path = REPLACE(path, ?, ?)
                 WHERE job_id = ?
                   AND path LIKE ?
                """,
                (old_prefix, new_prefix, rec.id, like),
            )
        touched += 1

    print(f"migrated_jobs: {touched}")
    print(f"skipped: {skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

