"""Reconcile disk artifacts and render (align must already be done)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

_env = ROOT / ".env"
if _env.is_file():
    for line in _env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v

from shorts_pipeline.config.settings import Settings
from shorts_pipeline.config.ui_store import effective_settings
from shorts_pipeline.context import set_job_id
from shorts_pipeline.jobs.image_recovery import (
    ensure_plan_artifact_from_disk,
    reconcile_image_artifacts_from_disk,
)
from shorts_pipeline.jobs.store import JobStore
from shorts_pipeline.orchestrator import PipelineOrchestrator


def main() -> int:
    job_id = sys.argv[1] if len(sys.argv) > 1 else None
    if not job_id:
        print("usage: python scripts/render_from_disk.py <job_id>")
        return 1

    settings = effective_settings(Settings())
    store = JobStore(settings.data_dir / "jobs.sqlite")
    set_job_id(job_id)
    ensure_plan_artifact_from_disk(store, settings, job_id)
    info = reconcile_image_artifacts_from_disk(store, settings, job_id)
    print("reconciled:", info)

    orch = PipelineOrchestrator(settings, store)
    print("rendering ...")
    out = orch.run_render(job_id)
    print("render done:", out)
    print("publish + telegram ...")
    orch.run_publish(job_id)
    print("publish done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
