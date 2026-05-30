"""Bulk Telegram send — all completed jobs that have final.mp4 + publish_package.json
but no .tg_sent marker yet.

Sends them in chronological order (oldest first), exactly like the pipeline does.
Skips:
  - jobs in trash/ subfolder
  - jobs already marked .tg_sent

Usage:
  python scripts/bulk_tg_send.py
  python scripts/bulk_tg_send.py --dry-run
  python scripts/bulk_tg_send.py --force     # resend even if .tg_sent exists
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

_env = ROOT / ".env"
if _env.is_file():
    for line in _env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val

from shorts_pipeline.config.settings import Settings  # noqa: E402
from shorts_pipeline.tg_notify import send_job  # noqa: E402


def _read_plan(job_dir: Path) -> dict:
    p = job_dir / "plan.json"
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def collect_jobs(jobs_root: Path) -> list[tuple[float, Path, str, str]]:
    """Return (mtime, job_dir, figure, niche) sorted oldest-first, excluding trash."""
    rows: list[tuple[float, Path, str, str]] = []
    for pkg in jobs_root.rglob("publish_package.json"):
        job_dir = pkg.parent
        # skip trash
        if "trash" in [p.lower() for p in job_dir.parts]:
            continue
        final = job_dir / "final.mp4"
        if not final.is_file():
            continue
        plan = _read_plan(job_dir)
        figure = (plan.get("historical_figure") or job_dir.name).strip()
        niche = (plan.get("niche") or "").strip() or None
        mtime = job_dir.stat().st_mtime
        rows.append((mtime, job_dir, figure, niche or ""))
    rows.sort(key=lambda x: x[0])
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="List jobs but do not send")
    ap.add_argument("--force", action="store_true", help="Resend even if already sent")
    ap.add_argument("--no-archive", action="store_true", help="Skip operator zip")
    ap.add_argument("--stop-at", default="", metavar="NAME",
                    help="Stop after the first job whose figure name contains NAME (case-insensitive)")
    args = ap.parse_args()

    s = Settings()
    jobs_root = s.data_dir / "jobs"

    if not s.tg_api_id or not s.tg_api_hash or not s.tg_target_chat:
        print("[ERROR] TG credentials missing — set SHORTS_TG_API_ID / TG_API_HASH / TG_TARGET_CHAT in .env")
        return 1

    all_jobs = collect_jobs(jobs_root)
    if not all_jobs:
        print("No jobs found with final.mp4 + publish_package.json (outside trash).")
        return 0

    stop_at = args.stop_at.strip().lower()

    to_send = []
    skipped_sent = []
    for mtime, job_dir, figure, niche in all_jobs:
        already_sent = (job_dir / ".tg_sent").is_file()
        if already_sent and not args.force:
            skipped_sent.append((job_dir, figure))
        else:
            to_send.append((job_dir, figure, niche or None))
        # stop-at: include this job then break
        if stop_at and stop_at in figure.lower():
            break

    print(f"Jobs found:        {len(all_jobs)}")
    print(f"Already sent:      {len(skipped_sent)}")
    print(f"Will send now:     {len(to_send)}")
    if args.force:
        print("  --force: resending all (including already sent)")
    print()

    for job_dir, figure, niche in to_send:
        already = (job_dir / ".tg_sent").is_file()
        tag = "[resend]" if already else "[new]"
        print(f"  {tag} {figure}  ({niche or 'unknown niche'})  — {job_dir.name}")

    if args.dry_run:
        print("\n[dry-run] Exiting without sending.")
        return 0

    if not to_send:
        print("Nothing to send.")
        return 0

    print(f"\nStarting send — {len(to_send)} job(s) ...\n")

    topics_path = Path(s.tg_topics_path) if getattr(s, "tg_topics_path", None) else None
    send_archive = not args.no_archive and s.tg_send_archive

    ok = fail = 0
    for i, (job_dir, figure, niche) in enumerate(to_send, 1):
        pkg = job_dir / "publish_package.json"
        print(f"[{i}/{len(to_send)}] Sending: {figure} ...", flush=True)
        t0 = time.time()
        try:
            send_job(
                api_id=int(s.tg_api_id),
                api_hash=s.tg_api_hash,
                session_path=str(ROOT / s.tg_session_path),
                target_chat=str(s.tg_target_chat),
                job_dir=job_dir,
                package_json=pkg,
                send_archive=send_archive,
                archive_max_mb=s.tg_archive_max_mb,
                niche=niche,
                topics_path=topics_path if (topics_path and topics_path.is_file()) else None,
                forum_topics_enabled=s.tg_forum_topics_enabled,
                max_retries=s.tg_send_max_retries,
                force_resend=args.force,
            )
            elapsed = round(time.time() - t0, 1)
            print(f"  OK  {elapsed}s", flush=True)
            ok += 1
        except Exception as exc:
            elapsed = round(time.time() - t0, 1)
            print(f"  FAIL  {elapsed}s  {exc}", flush=True)
            fail += 1

        # Brief pause between jobs so Telegram doesn't flood-wait us
        if i < len(to_send):
            time.sleep(3)

    print(f"\nDone — sent={ok}  failed={fail}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
