"""Re-render an existing job's final.mp4 from its current images + narration +
clause_timings, applying the current editor.pacing law. Does NOT touch images,
TTS, or alignment.

Usage:
  python scripts/rerender_only.py christopher-columbus-2ff6beb5
"""

from __future__ import annotations

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
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v

from shorts_pipeline.config.settings import Settings
from shorts_pipeline.config.ui_store import effective_settings
from shorts_pipeline.context import set_job_id
from shorts_pipeline.jobs.store import JobStore
from shorts_pipeline.orchestrator import PipelineOrchestrator


def main() -> int:
    job_id = sys.argv[1] if len(sys.argv) > 1 else None
    if not job_id:
        print("usage: python scripts/rerender_only.py <job_id>")
        return 1

    settings = effective_settings(Settings())
    store = JobStore(settings.data_dir / "jobs.sqlite")
    orch = PipelineOrchestrator(settings, store)
    set_job_id(job_id)

    t = time.time()
    print(f"Re-rendering {job_id} (pacing applied at render time) ...")
    out = orch.run_render(job_id)
    print(f"done in {time.time()-t:.1f}s -> {out}")
    if Path(out).exists():
        print(f"size {Path(out).stat().st_size//1024//1024} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
