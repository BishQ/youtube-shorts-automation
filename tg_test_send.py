"""Send an existing completed job folder to Telegram (test / backfill).

Usage:
    python tg_test_send.py plato-48bd1827
    python tg_test_send.py --all          # every job with final.mp4 + publish_package.json
    python tg_test_send.py --list
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from shorts_pipeline.config.settings import Settings  # noqa: E402
from shorts_pipeline.tg_notify import send_job  # noqa: E402
from shorts_pipeline.tg_topics import resolve_job_niche  # noqa: E402


def _ready_jobs() -> list[Path]:
    jobs_dir = Settings().data_dir / "jobs"
    out: list[Path] = []
    for d in sorted(jobs_dir.iterdir()):
        if not d.is_dir() or d.name in ("trash", "batch_001", "parallel"):
            continue
        if (d / "final.mp4").is_file() and (d / "publish_package.json").is_file():
            out.append(d)
    return out


def _send_one(job_dir: Path, settings: Settings) -> None:
    niche = resolve_job_niche(
        job_dir,
        default_niche=settings.tg_default_niche,
    )
    figure = ""
    plan = job_dir / "plan.json"
    if plan.is_file():
        figure = json.loads(plan.read_text(encoding="utf-8")).get("historical_figure", "")

    print(f"\n>>> Sending {job_dir.name}  ({figure or 'unknown'})  niche={niche or 'General'}")
    send_job(
        api_id=settings.tg_api_id,
        api_hash=settings.tg_api_hash,
        session_path=settings.tg_session_path,
        target_chat=settings.tg_target_chat,
        job_dir=job_dir,
        package_json=job_dir / "publish_package.json",
        send_archive=settings.tg_send_archive,
        archive_max_mb=settings.tg_archive_max_mb,
        niche=niche,
        topics_path=settings.tg_topics_path,
        forum_topics_enabled=settings.tg_forum_topics_enabled,
        max_retries=settings.tg_send_max_retries,
        force_resend=True,
    )
    print(f"    Done: {job_dir.name}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Test Telegram send for existing jobs.")
    ap.add_argument("job", nargs="?", help="Job folder name, e.g. plato-48bd1827")
    ap.add_argument("--all", action="store_true", help="Send all ready jobs")
    ap.add_argument("--list", action="store_true", help="List ready jobs")
    args = ap.parse_args()

    settings = Settings()
    if not settings.tg_api_id or not settings.tg_api_hash or not settings.tg_target_chat:
        print("Telegram not configured in .env", file=sys.stderr)
        return 1

    ready = _ready_jobs()
    if args.list or (not args.job and not args.all):
        print(f"Ready jobs ({len(ready)}):")
        for d in ready:
            niche = resolve_job_niche(d, default_niche=settings.tg_default_niche) or "General"
            print(f"  {d.name}  niche={niche}")
        if not args.list and not args.job and not args.all:
            print("\nRun:  python tg_test_send.py <job-name>")
        return 0

    if args.all:
        for d in ready:
            _send_one(d, settings)
        return 0

    job_dir = settings.data_dir / "jobs" / args.job
    if not job_dir.is_dir():
        print(f"Job not found: {job_dir}", file=sys.stderr)
        return 1
    _send_one(job_dir, settings)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
