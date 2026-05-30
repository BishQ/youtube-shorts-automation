"""Create a niche-organized view of data/jobs without moving any files.

This script reads job metadata from data/jobs.sqlite and creates Windows junctions:
  data/jobs_by_niche/<niche>/<job_id>  ->  data/jobs/<job_id>

Why junctions?
  - No changes to pipeline paths (artifacts.path in sqlite stays valid).
  - Safe while jobs are running.
  - Fast to regenerate; deleting the view never deletes the real jobs.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


# Repo root on path (match other scripts' pattern)
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))


@dataclass(frozen=True)
class LinkSpec:
    niche: str
    job_id: str
    target: Path
    link: Path


def _safe_niche(s: str) -> str:
    s = (s or "").strip().lower()
    return s or "unknown"


def _is_windows() -> bool:
    return os.name == "nt"


def _mk_junction(link: Path, target: Path) -> None:
    """Create a directory junction (Windows) or symlink (others)."""
    link.parent.mkdir(parents=True, exist_ok=True)

    if link.exists() or link.is_symlink():
        raise FileExistsError(str(link))

    if _is_windows():
        # mklink requires cmd.exe, and junctions do not require admin privileges.
        res = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
        )
        if res.returncode != 0:
            msg = (res.stderr or res.stdout or "").strip()
            raise RuntimeError(f"mklink failed ({res.returncode}): {msg}")
        return

    link.symlink_to(target, target_is_directory=True)


def _remove_link(path: Path) -> None:
    """Remove junction/symlink directory safely (never touches target)."""
    if not path.exists() and not path.is_symlink():
        return
    # Junctions appear as directories. rmtree removes only the link itself.
    if path.is_symlink():
        path.unlink()
        return
    shutil.rmtree(path)


def _load_jobs(db_path: Path, limit: int) -> list[tuple[str, str, str]]:
    """Return [(job_id, topic_type, status), ...] newest-first."""
    from shorts_pipeline.jobs.store import JobStore

    store = JobStore(db_path)
    rows = store.list_jobs(limit=limit)
    out: list[tuple[str, str, str]] = []
    for r in rows:
        topic_type = getattr(r, "topic_type", "") or ""
        status = getattr(r, "status", None)
        status_s = getattr(status, "value", str(status)) if status is not None else ""
        out.append((r.id, topic_type, status_s))
    return out


def _build_links(
    *,
    jobs_root: Path,
    out_root: Path,
    db_path: Path,
    limit: int,
    include_running: bool,
) -> list[LinkSpec]:
    links: list[LinkSpec] = []
    for job_id, topic_type, status in _load_jobs(db_path, limit=limit):
        if (not include_running) and status in ("running", "paused", "pending"):
            continue
        from shorts_pipeline.jobs.paths import niche_folder_name

        niche = _safe_niche(niche_folder_name(topic_type))
        target = (jobs_root / job_id).resolve()
        link = (out_root / niche / job_id).resolve()
        links.append(LinkSpec(niche=niche, job_id=job_id, target=target, link=link))
    return links


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=5000, help="max jobs to index (newest-first)")
    ap.add_argument(
        "--include-running",
        action="store_true",
        help="also link running/paused/pending jobs (default: skip them)",
    )
    ap.add_argument(
        "--refresh",
        action="store_true",
        help="delete and recreate the whole jobs_by_niche folder",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="print what would change without creating links",
    )
    args = ap.parse_args()

    data_dir = _ROOT / "data"
    jobs_root = data_dir / "jobs"
    db_path = data_dir / "jobs.sqlite"
    out_root = data_dir / "jobs_by_niche"

    if not db_path.is_file():
        print(f"ERROR: sqlite not found: {db_path}", file=sys.stderr)
        return 2
    if not jobs_root.is_dir():
        print(f"ERROR: jobs folder not found: {jobs_root}", file=sys.stderr)
        return 2

    if args.refresh:
        if args.dry_run:
            print(f"[dry-run] would delete: {out_root}")
        else:
            _remove_link(out_root)

    links = _build_links(
        jobs_root=jobs_root,
        out_root=out_root,
        db_path=db_path,
        limit=args.limit,
        include_running=bool(args.include_running),
    )

    created = 0
    skipped_missing_target = 0
    skipped_existing = 0

    for spec in links:
        if not spec.target.is_dir():
            skipped_missing_target += 1
            continue
        if spec.link.exists() or spec.link.is_symlink():
            skipped_existing += 1
            continue
        if args.dry_run:
            print(f"[dry-run] link {spec.link} -> {spec.target}")
            created += 1
            continue
        try:
            _mk_junction(spec.link, spec.target)
            created += 1
        except Exception as exc:
            print(f"ERROR: could not link {spec.link} -> {spec.target}: {exc}", file=sys.stderr)
            return 3

    print(f"out_root: {out_root}")
    print(f"created: {created}")
    print(f"skipped_existing: {skipped_existing}")
    print(f"skipped_missing_target: {skipped_missing_target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

