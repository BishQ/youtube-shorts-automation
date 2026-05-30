"""Resume plan-only jobs through the full pipeline (images → publish).

Finds SQLite jobs stuck at last_completed_stage=plan (e.g. from
bulk_documentary_plans_gemma.py) and runs PipelineRunner.run_job for each.

Usage:
  python scripts/bulk_resume_plans_pipeline.py --dry-run
  python scripts/bulk_resume_plans_pipeline.py --limit 3
  python scripts/bulk_resume_plans_pipeline.py
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

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
from shorts_pipeline.config.ui_store import effective_settings  # noqa: E402
from shorts_pipeline.jobs.models import (  # noqa: E402
    ArtifactType,
    JobStatus,
    PipelineStage,
)
from shorts_pipeline.jobs.pipeline_runner import PipelineRunner  # noqa: E402
from shorts_pipeline.jobs.store import JobStore  # noqa: E402


def _plan_only_jobs(store: JobStore) -> list[str]:
    out: list[str] = []
    for job in store.list_jobs(limit=50_000):
        if job.last_completed_stage != PipelineStage.plan:
            continue
        if job.status not in (JobStatus.pending, JobStatus.paused, JobStatus.failed):
            continue
        art = store.get_latest_artifact(job.id, PipelineStage.plan, ArtifactType.plan_json)
        if art is None:
            continue
        if not Path(art.path).is_file():
            continue
        out.append(job.id)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=0, help="max jobs to run (0 = all)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--job-id", action="append", default=[], help="only these job ids")
    args = ap.parse_args()

    settings = effective_settings(Settings())
    store = JobStore(settings.data_dir / "jobs.sqlite")
    if args.job_id:
        job_ids = list(args.job_id)
    else:
        job_ids = _plan_only_jobs(store)

    if args.limit:
        job_ids = job_ids[: args.limit]

    print(f"plan-only jobs to resume: {len(job_ids)}", flush=True)
    if not job_ids:
        return 0

    if args.dry_run:
        for jid in job_ids:
            j = store.get_job(jid)
            fig = j.figure_name if j else "?"
            print(f"  would run: {jid} ({fig})", flush=True)
        return 0

    runner = PipelineRunner(settings, store)
    ok = fail = 0
    for i, jid in enumerate(job_ids, 1):
        j = store.get_job(jid)
        fig = j.figure_name if j else "?"
        print(f"\n[{i}/{len(job_ids)}] {jid} — {fig}", flush=True)
        t0 = time.time()
        try:
            runner.run_job(jid)
            rec = store.get_job(jid)
            st = rec.status.value if rec else "?"
            print(f"  done status={st} in {(time.time() - t0) / 60:.1f} min", flush=True)
            if rec and rec.status == JobStatus.completed:
                ok += 1
            else:
                fail += 1
        except Exception as exc:
            fail += 1
            print(f"  FAIL: {str(exc)[:300]}", flush=True)

    print(f"\nFinished — ok={ok} fail={fail}", flush=True)
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
