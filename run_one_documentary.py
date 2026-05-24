"""End-to-end run for the FIRST documentary topic.

Runs: planner -> image generation -> Wan I2V motion clips -> Kokoro TTS ->
alignment -> ASS subtitles -> FFmpeg render -> final.mp4.

Reads the first topic from:
    topics/famous_people_1000/batch_001/topics.txt

Usage:
    python run_one_documentary.py

Optional:
    python run_one_documentary.py --topic "Robert Ballard"   # override
    python run_one_documentary.py --batch batch_002          # use a different batch

Prerequisites (must all be running BEFORE you start this):
    - Ollama on http://127.0.0.1:11434  (planner LLM)
    - ComfyUI on http://127.0.0.1:8188  (Qwen image + Wan I2V workflows)
    - Kokoro TTS model files in the expected path (settings.kokoro_*)
    - BGM file present (assets/bgm.wav or extra tools/bgm.mp3)

Output:
    data/jobs/<slug>-<8hex>/final.mp4   <- the final Short
    + plan.json, narration.wav, clause_timings.json, subtitles.ass,
      images/*.png, videos/*.mp4 along the way
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from shorts_pipeline.config.settings import Settings
from shorts_pipeline.config.ui_store import effective_settings
from shorts_pipeline.jobs.models import JobConfigSnapshot, JobStatus
from shorts_pipeline.jobs.state_machine import StageRunner
from shorts_pipeline.jobs.store import JobStore
from shorts_pipeline.logging_setup import get_logger
from shorts_pipeline.orchestrator import orchestrator_stage_handler

log = get_logger(__name__)


def _first_topic(batch: str) -> str:
    path = ROOT / "topics" / "famous_people_1000" / batch / "topics.txt"
    if not path.is_file():
        raise SystemExit(f"topics file not found: {path}")
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        # strip leading "1. " etc.
        if line[:3].rstrip(".").isdigit():
            line = line.split(".", 1)[1].strip()
        return line
    raise SystemExit(f"no topics in {path}")


def _resolve_bgm(settings: Settings) -> Path:
    candidates = [
        settings.default_bgm_path,
        ROOT / "assets" / "bgm.wav",
        ROOT / "extra tools" / "bgm.mp3",
    ]
    for c in candidates:
        if not c:
            continue
        p = Path(c)
        if p.is_file():
            return p.resolve()
    raise SystemExit(
        "No BGM file found. Set SHORTS_DEFAULT_BGM_PATH in .env or place a "
        "file at assets/bgm.wav."
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--topic", default=None, help="override topic (default: first line of batch)")
    ap.add_argument("--batch", default="batch_001", help="documentary batch folder (default: batch_001)")
    ap.add_argument("--poll-secs", type=int, default=10, help="status poll interval")
    args = ap.parse_args()

    topic = args.topic or _first_topic(args.batch)
    print(f"=== Running end-to-end for documentary topic: {topic!r} ===")

    base = Settings()
    settings = effective_settings(base)
    bgm = _resolve_bgm(settings)
    print(f"BGM: {bgm}")

    db_path = settings.data_dir / "jobs.sqlite"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    store = JobStore(db_path)
    handler = orchestrator_stage_handler(settings, store)
    runner = StageRunner(store, handler, settings=settings)

    cfg = JobConfigSnapshot(
        figure_name=topic,
        topic_type="historical_figure",
        language="en",
        bgm_path=str(bgm),
        watermark_enabled=False,
        end_plate_enabled=True,
        comfy_workflow_name=settings.comfy_workflow_name,
        overlay_enabled=True,
    )
    job_id = store.create_job(cfg)
    print(f"job_id: {job_id}")

    started = time.time()
    try:
        runner.resume_job(job_id)
    except Exception as exc:
        print(f"\n!! pipeline raised: {type(exc).__name__}: {exc}")
        record = store.get_job(job_id)
        if record and record.error:
            print(f"   stage={record.error.stage} code={record.error.code} msg={record.error.message}")
        return 2

    elapsed = time.time() - started
    record = store.get_job(job_id)
    print(f"\n=== finished in {elapsed:.0f}s — status={record.status.value} ===")
    if record.status == JobStatus.completed:
        final_dir = ROOT / "data" / "jobs" / job_id
        final = final_dir / "final.mp4"
        print(f"final video: {final if final.exists() else final_dir!s} (exists={final.exists()})")
        return 0
    if record.error:
        print(f"error: stage={record.error.stage} code={record.error.code}")
        print(f"       message={record.error.message}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
