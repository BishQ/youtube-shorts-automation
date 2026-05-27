"""Render TTS narration only for all plans under scripts_out/gemma_volume/.

For each successful plan JSON, create a job and run just the TTS stage.
Outputs: data/jobs/<job_id>/narration.wav

Run:  python scripts/bulk_tts_from_plans.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from shorts_pipeline.config.settings import Settings  # noqa: E402
from shorts_pipeline.config.ui_store import effective_settings  # noqa: E402
from shorts_pipeline.context import set_job_id  # noqa: E402
from shorts_pipeline.jobs.models import (  # noqa: E402
    ArtifactType, JobConfigSnapshot, JobStatus, PipelineStage,
)
from shorts_pipeline.jobs.store import JobStore  # noqa: E402
from shorts_pipeline.orchestrator import PipelineOrchestrator  # noqa: E402
from shorts_pipeline.planner.schema import NarrationPlan  # noqa: E402

SRC_ROOT = ROOT / "scripts_out" / "gemma_volume"
TTS_OUT = ROOT / "scripts_out" / "bulk_tts_audio"
TTS_OUT.mkdir(parents=True, exist_ok=True)


def _gather_plans() -> list[tuple[str, Path, dict]]:
    """Collect all successful plan JSONs. Returns list of (label, src_path, plan_dict)."""
    rows: list[tuple[str, Path, dict]] = []
    for jf in sorted(SRC_ROOT.glob("*/*/*.json")):
        try:
            d = json.loads(jf.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not d.get("_meta", {}).get("success"):
            continue
        plan = d.get("plan")
        if not plan:
            continue
        # label: gemma-q4__history__04
        parts = jf.relative_to(SRC_ROOT).with_suffix("").parts
        label = "__".join(parts)
        rows.append((label, jf, plan))
    return rows


def _resolve_bgm(settings: Settings) -> Path:
    # Use any existing wav in workflows dir as BGM placeholder (TTS doesn't need real BGM)
    bgm_candidates = list((ROOT / "workflows").glob("*.wav")) + list((ROOT / "assets").glob("*.wav"))
    if bgm_candidates:
        return bgm_candidates[0]
    # Create empty BGM
    import wave
    bgm = settings.data_dir / "_dummy_bgm.wav"
    if not bgm.exists():
        bgm.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(bgm), "w") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050)
            w.writeframes(b"\x00\x00" * 22050)
    return bgm


def main() -> int:
    base = Settings()
    settings = effective_settings(base)
    store = JobStore(settings.data_dir / "jobs.sqlite")
    bgm = _resolve_bgm(settings)

    plans = _gather_plans()
    print(f"Found {len(plans)} successful plans to TTS")
    if not plans:
        return 0

    orch = PipelineOrchestrator(settings, store)
    done = 0
    t_start = time.time()
    for label, src_path, plan in plans:
        out_path = TTS_OUT / f"{label}.wav"
        if out_path.exists():
            print(f"  [{done+1}/{len(plans)}] skip exists: {label}")
            done += 1; continue

        # Validate (with allow_figure_name)
        try:
            niche = src_path.parent.name
            validated = NarrationPlan.model_validate(
                plan, context={"allow_figure_name": True, "niche": niche})
            plan_json = validated.model_dump(mode="json")
        except Exception as e:
            print(f"  [{done+1}/{len(plans)}] validation skip {label}: {e}")
            done += 1; continue

        # Create job
        job_id = store.create_job(JobConfigSnapshot(
            figure_name=plan_json.get("historical_figure", label),
            topic_type="historical_figure",
            language="en",
            bgm_path=str(bgm),
            watermark_enabled=False,
            end_plate_enabled=False,
            overlay_enabled=False,
        ))
        set_job_id(job_id)

        # Write plan.json + register
        jd = (settings.data_dir / "jobs" / job_id).resolve()
        jd.mkdir(parents=True, exist_ok=True)
        plan_file = jd / "plan.json"
        plan_file.write_text(json.dumps(plan_json, ensure_ascii=False, indent=2), encoding="utf-8")
        store.add_artifact(job_id, PipelineStage.plan, ArtifactType.plan_json, plan_file, meta={"source": "bulk_tts"})
        store.update_job_progress(
            job_id, status=JobStatus.running,
            current_stage=PipelineStage.tts,
            last_completed_stage=PipelineStage.plan, clear_error=True,
        )

        t0 = time.time()
        try:
            orch.run_tts(job_id)
            # Copy narration.wav out
            narration = jd / "narration.wav"
            if narration.exists():
                import shutil
                shutil.copy2(narration, out_path)
                size_kb = out_path.stat().st_size // 1024
                print(f"  [{done+1}/{len(plans)}] OK {label} ({time.time()-t0:.1f}s, {size_kb}KB)", flush=True)
            else:
                print(f"  [{done+1}/{len(plans)}] FAIL {label}: no narration.wav produced", flush=True)
        except Exception as e:
            print(f"  [{done+1}/{len(plans)}] FAIL {label}: {str(e)[:200]}", flush=True)
        done += 1

    elapsed = time.time() - t_start
    files = list(TTS_OUT.glob("*.wav"))
    print(f"\nDONE — {len(files)} narration.wav files in {TTS_OUT}  ({elapsed/60:.1f} min)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
