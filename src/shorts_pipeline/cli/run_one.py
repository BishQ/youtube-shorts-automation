"""Run one documentary topic through the SQLite-backed pipeline."""

from __future__ import annotations

import argparse
import re
import time
from pathlib import Path

from shorts_pipeline.config.settings import Settings
from shorts_pipeline.config.ui_store import effective_settings
from shorts_pipeline.jobs.models import JobConfigSnapshot, JobStatus
from shorts_pipeline.jobs.pipeline_runner import PipelineRunner
from shorts_pipeline.jobs.store import JobStore


def _normalize_topic(line: str) -> str:
    return re.sub(r"^\s*\d+[\.)]\s*", "", line).strip()


def _first_topic(root: Path, batch: str) -> str:
    path = root / "topics" / "famous_people_1000" / batch / "topics.txt"
    if not path.is_file():
        raise SystemExit(f"topics file not found: {path}")
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line:
            return _normalize_topic(line)
    raise SystemExit(f"no topics in {path}")


def _resolve_bgm(root: Path, settings: Settings) -> Path:
    for candidate in (settings.default_bgm_path, root / "assets" / "bgm.wav", root / "extra tools" / "bgm.mp3"):
        if not candidate:
            continue
        path = Path(candidate)
        if path.is_file():
            return path.resolve()
    raise SystemExit("No BGM file found. Set SHORTS_DEFAULT_BGM_PATH or add assets/bgm.wav.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--topic", default=None)
    parser.add_argument("--batch", default="batch_001")
    args = parser.parse_args()

    root = Path.cwd()
    base = Settings()
    settings = effective_settings(base)
    topic = _normalize_topic(args.topic) if args.topic else _first_topic(root, args.batch)
    bgm = _resolve_bgm(root, settings)
    store = JobStore(settings.data_dir / "jobs.sqlite")
    job_id = store.create_job(
        JobConfigSnapshot(
            figure_name=topic,
            topic_type="historical_figure",
            language="en",
            bgm_path=str(bgm),
            watermark_enabled=False,
            end_plate_enabled=True,
            comfy_workflow_name=settings.comfy_workflow_name,
            overlay_enabled=True,
        )
    )
    print(f"job_id: {job_id}")
    started = time.time()
    PipelineRunner(base, store).run_job(job_id)
    job = store.get_job(job_id)
    elapsed = time.time() - started
    status = job.status if job else "missing"
    print(f"finished in {elapsed:.0f}s status={status}")
    return 0 if job and job.status == JobStatus.completed else 1


if __name__ == "__main__":
    raise SystemExit(main())
