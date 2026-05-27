"""Run only the plan (script) stage — test LM Studio + full narration plan."""

from __future__ import annotations

import argparse
import json
import os
import sys
import wave
from pathlib import Path

# Repo root on path
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

_env = _ROOT / ".env"
if _env.is_file():
    for line in _env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key.startswith("SHORTS_"):
            os.environ[key] = val
        elif key and key not in os.environ:
            os.environ[key] = val

import os  # noqa: E402 — after optional .env load

from shorts_pipeline.config.settings import Settings
from shorts_pipeline.jobs.models import JobConfigSnapshot, PipelineStage
from shorts_pipeline.jobs.store import JobStore
from shorts_pipeline.orchestrator import PipelineOrchestrator


def _ensure_dummy_bgm(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.stat().st_size > 44:
        return path
    with wave.open(str(path), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(22050)
        w.writeframes(b"\x00\x00" * 22050)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--figure",
        default="Genghis Khan",
        help="Historical figure name for the short",
    )
    args = parser.parse_args()

    settings = Settings()
    store = JobStore(settings.data_dir / "jobs.sqlite")
    bgm = _ensure_dummy_bgm(settings.data_dir / "_test_bgm.wav")

    job_id = store.create_job(
        JobConfigSnapshot(
            figure_name=args.figure,
            topic_type="historical_figure",
            language="en",
            bgm_path=str(bgm),
            watermark_enabled=False,
            end_plate_enabled=True,
            overlay_enabled=False,
        )
    )
    print(f"job_id: {job_id}")
    print(f"LM: {settings.local_llm_base_url}  model: {settings.local_llm_model}")
    print(f"figure: {args.figure}")
    print("Generating full narration plan (may take 1-3 min)...")

    from shorts_pipeline.planner.vllm_client import VllmPlannerError

    orch = PipelineOrchestrator(settings, store)
    try:
        orch.run_plan(job_id)
    except VllmPlannerError as exc:
        if isinstance(exc.detail, dict) and exc.detail.get("full_script"):
            fail_path = settings.data_dir / "jobs" / job_id / "plan.failed.json"
            fail_path.parent.mkdir(parents=True, exist_ok=True)
            fail_path.write_text(json.dumps(exc.detail, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"\nLast attempt saved (validation failed): {fail_path}", file=sys.stderr)
            print("\n--- full_script (preview) ---\n", exc.detail.get("full_script", "")[:2000])
        raise

    plan_path = settings.data_dir / "jobs" / job_id / "plan.json"
    if not plan_path.is_file():
        print("ERROR: plan.json not created", file=sys.stderr)
        return 1

    data = json.loads(plan_path.read_text(encoding="utf-8"))
    full = data.get("full_script", "")
    clauses = data.get("clauses", [])

    print("\n" + "=" * 60)
    print("FULL SCRIPT (merge-ready narration)")
    print("=" * 60)
    print(full)
    print("=" * 60)
    print(f"Words: {len(full.split())}  |  Clauses: {len(clauses)}")
    print(f"Saved: {plan_path}")
    print("\nOpen Web UI -> job -> Script tab to edit, or continue pipeline from images.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
