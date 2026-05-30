"""Fit an existing narration.wav to the duration band, then re-align + re-render.

Uses the new narration auto-fit (atempo, pitch-preserved) on the CURRENT
narration.wav — no TTS regen, no Kokoro server needed. Then Whisper re-align
(so subtitles + clause timings match the fitted audio) and FFmpeg render.

Usage:
  python scripts/refit_align_render.py christopher-columbus-2ff6beb5
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
from shorts_pipeline.tts_worker.narration_fit import fit_narration


def main() -> int:
    job_id = sys.argv[1] if len(sys.argv) > 1 else None
    if not job_id:
        print("usage: python scripts/refit_align_render.py <job_id>")
        return 1

    settings = effective_settings(Settings())
    store = JobStore(settings.data_dir / "jobs.sqlite")
    orch = PipelineOrchestrator(settings, store)
    set_job_id(job_id)

    jd = orch._job_dir(job_id)
    wav = jd / "narration.wav"
    if not wav.is_file():
        print(f"narration.wav not found: {wav}")
        return 1

    print(f"[1/3] fit narration ({wav}) ...")
    r = fit_narration(wav, settings)
    print(f"  {r.original_s:.2f}s -> {r.final_s:.2f}s  atempo={r.atempo:.3f}  ({r.action})")

    print("[2/3] re-align (Whisper) ...")
    t = time.time()
    orch.run_align(job_id)
    print(f"  done in {time.time()-t:.1f}s")

    print("[3/4] render ...")
    t = time.time()
    out = orch.run_render(job_id)
    print(f"  done in {time.time()-t:.1f}s -> {out}")

    print("[4/4] publish + telegram ...")
    t = time.time()
    orch.run_publish(job_id)
    print(f"  done in {time.time()-t:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
