"""Wan-less documentary start: plan JSON → Grok images → Comfy upscale → TTS → render.

Default: every clause image via Grok Imagine (SHORTS_GROK_ONLY=1), then Comfy upscale.
Use --mixed-scenes to send figure_present=false clauses to Together/Comfy instead.

Prerequisites:
  - SHORTS_GROK_API_KEY in .env
  - ComfyUI running at SHORTS_COMFY_BASE_URL (for upscale after Grok)
  - BGM file (SHORTS_DEFAULT_BGM_PATH or assets/bgm.wav)

Usage:
  python scripts/1huniiwanguistart.py --job-id christopher-columbus-2ff6beb5
  python scripts/1huniiwanguistart.py --plan path/to/plan.json
  python scripts/1huniiwanguistart.py --job-id JOB --stages images
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

_ENV = ROOT / ".env"
if _ENV.is_file():
    for raw in _ENV.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def _apply_wanless_grok_upscale_profile(*, grok_only: bool) -> None:
    """This script's pipeline profile (overrides .env for these keys)."""
    os.environ["SHORTS_IMAGE_BACKEND"] = "smart_grok"
    os.environ["SHORTS_I2V_ENABLED"] = "0"
    os.environ["SHORTS_SKIP_COMFY_UPSCALE"] = "0"
    os.environ["SHORTS_OUTRO_IMAGE_ENABLED"] = "0"
    if grok_only:
        os.environ["SHORTS_GROK_ONLY"] = "1"
    else:
        os.environ.pop("SHORTS_GROK_ONLY", None)


def _resolve_bgm(root: Path, settings) -> Path:
    for candidate in (
        settings.default_bgm_path,
        root / "assets" / "bgm.wav",
        root / "extra tools" / "bgm.mp3",
    ):
        if not candidate:
            continue
        p = Path(candidate)
        if p.is_file():
            return p.resolve()
    raise SystemExit(
        "No BGM file found. Set SHORTS_DEFAULT_BGM_PATH or add assets/bgm.wav."
    )


def _beat_for_index(i: int, total: int) -> dict[str, Any]:
    if i == 0:
        return {
            "emotion": "hook",
            "intensity": 0.9,
            "camera": "ken_burns",
            "transition_in": "hard_cut",
            "duration_hint": "short",
            "color_grade": "dark_thriller",
            "audio_event": "low_rumble",
            "visual_tier": "legendary",
            "subtitle_position": "bottom",
            "cut_target": None,
        }
    if i == total - 1:
        return {
            "emotion": "reflective",
            "intensity": 0.65,
            "camera": "hold",
            "transition_in": "xfade",
            "duration_hint": "long",
            "color_grade": "golden_hour",
            "audio_event": "none",
            "visual_tier": "grounded",
            "subtitle_position": "bottom",
            "cut_target": None,
        }
    camera_cycle = ["ken_burns", "pan", "zoom_out", "hold", "parallax"]
    emotion = (
        "tense_buildup"
        if i < (total * 0.45)
        else "suspense"
        if i < (total * 0.75)
        else "climactic"
    )
    return {
        "emotion": emotion,
        "intensity": 0.72 if emotion != "climactic" else 0.9,
        "camera": camera_cycle[i % len(camera_cycle)],
        "transition_in": "xfade",
        "duration_hint": "medium",
        "color_grade": "dark_thriller" if i % 2 else "tragic_cold",
        "audio_event": "none",
        "visual_tier": "cinematic",
        "subtitle_position": "bottom",
        "cut_target": None,
    }


def _build_validated_plan(raw: dict[str, Any]) -> dict[str, Any]:
    from shorts_pipeline.planner.schema import NarrationPlan

    clauses_in = raw.get("clauses") if isinstance(raw.get("clauses"), list) else []
    clauses: list[dict[str, Any]] = []
    clause_texts: list[str] = []
    for idx, c in enumerate(clauses_in):
        if not isinstance(c, dict):
            continue
        txt = str(c.get("text") or "").strip()
        clause_texts.append(txt)
        beat = c.get("beat") if isinstance(c.get("beat"), dict) else _beat_for_index(idx, len(clauses_in))
        clauses.append(
            {
                "text": txt,
                "image_prompt": str(c.get("image_prompt") or "").strip(),
                "beat": beat,
                "figure_present": bool(c.get("figure_present", True)),
            }
        )

    full_script = str(raw.get("full_script") or "").strip()
    if not full_script:
        full_script = " ".join(t for t in clause_texts if t).strip()

    plan_obj: dict[str, Any] = {
        "historical_figure": str(raw.get("historical_figure") or "").strip(),
        "cold_open_object": str(raw.get("cold_open_object") or "").strip(),
        "decision_lever": raw.get("decision_lever")
        if isinstance(raw.get("decision_lever"), dict)
        else {},
        "clauses": clauses,
        "full_script": full_script,
        "lut_choice": str(raw.get("lut_choice") or "dark_thriller").strip(),
        "end_plate_question": str(raw.get("end_plate_question") or "").strip(),
    }

    validated = NarrationPlan.model_validate(plan_obj, context={"allow_figure_name": True})
    return validated.model_dump(mode="json")


def _write_and_register_plan(
    *,
    store,
    settings,
    job_id: str,
    plan_json: dict[str, Any],
) -> Path:
    from shorts_pipeline.jobs.models import ArtifactType, JobStatus, PipelineStage
    from shorts_pipeline.jobs.paths import niche_folder_name, niche_job_dir

    rec = store.get_job(job_id)
    topic_type = rec.topic_type if rec else "historical_figure"
    jd = niche_job_dir(settings, niche_folder_name(topic_type), job_id)
    jd.mkdir(parents=True, exist_ok=True)
    path = jd / "plan.json"
    path.write_text(json.dumps(plan_json, ensure_ascii=False, indent=2), encoding="utf-8")
    store.add_artifact(
        job_id,
        PipelineStage.plan,
        ArtifactType.plan_json,
        path,
        meta={"source": "1huniiwanguistart"},
    )
    store.update_job_progress(
        job_id,
        status=JobStatus.pending,
        current_stage=PipelineStage.images,
        last_completed_stage=PipelineStage.plan,
        clear_error=True,
    )
    return path


def _run_stages(
    *,
    store,
    settings,
    job_id: str,
    stages: list[str],
    preflight: bool,
) -> None:
    from shorts_pipeline.context import set_job_id
    from shorts_pipeline.jobs.concurrency import PipelineProcessLock
    from shorts_pipeline.jobs.models import JobStatus, PipelineStage
    from shorts_pipeline.jobs.preflight import require_preflight
    from shorts_pipeline.jobs.paths import niche_folder_name, niche_job_dir, resolve_job_dir
    from shorts_pipeline.orchestrator import PipelineOrchestrator

    set_job_id(job_id)
    orch = PipelineOrchestrator(settings, store)

    with PipelineProcessLock(settings.data_dir):
        if preflight:
            require_preflight(settings)

        if "images" in stages:
            print("[images] All clauses → Grok, then Comfy upscale …", flush=True)
            store.update_job_progress(
                job_id,
                status=JobStatus.running,
                current_stage=PipelineStage.images,
                clear_error=True,
            )
            orch.run_images(job_id)
            store.update_job_progress(
                job_id,
                last_completed_stage=PipelineStage.images,
                current_stage=PipelineStage.tts,
            )
            print("[images] done", flush=True)

        if "tts" in stages:
            print("[tts] …", flush=True)
            store.update_job_progress(job_id, status=JobStatus.running, current_stage=PipelineStage.tts)
            orch.run_tts(job_id)
            store.update_job_progress(
                job_id,
                last_completed_stage=PipelineStage.tts,
                current_stage=PipelineStage.align,
            )
            print("[tts] done", flush=True)

        if "align" in stages:
            print("[align] …", flush=True)
            store.update_job_progress(job_id, status=JobStatus.running, current_stage=PipelineStage.align)
            orch.run_align(job_id)
            store.update_job_progress(
                job_id,
                last_completed_stage=PipelineStage.align,
                current_stage=PipelineStage.render,
            )
            print("[align] done", flush=True)

        if "render" in stages:
            print("[render] Ken Burns final.mp4 (Wan skipped) …", flush=True)
            store.update_job_progress(job_id, status=JobStatus.running, current_stage=PipelineStage.render)
            orch.run_render(job_id)
            store.update_job_progress(
                job_id,
                status=JobStatus.completed,
                last_completed_stage=PipelineStage.render,
                clear_current_stage=True,
                clear_error=True,
            )
            print("[render] done", flush=True)

    jd = resolve_job_dir(settings, store, job_id)
    final = jd / "final.mp4"
    if final.is_file():
        print(f"[ok] {final} ({final.stat().st_size // 1024 // 1024} MB)", flush=True)
    rec = store.get_job(job_id)
    niche = niche_folder_name(rec.topic_type if rec else "historical_figure")
    print(f"[job] id={job_id} dir={niche_job_dir(settings, niche, job_id)}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan", default=None, help="Path to exported plan.json (creates a new job)")
    ap.add_argument("--job-id", default=None, help="Existing job id (skip plan import)")
    ap.add_argument(
        "--stages",
        default="images,tts,align,render",
        help="Comma-separated: images, tts, align, render",
    )
    ap.add_argument("--preflight", action="store_true", help="Run environment preflight before pipeline")
    ap.add_argument(
        "--mixed-scenes",
        action="store_true",
        help="figure_present=false → Together/Comfy (default: all clauses Grok)",
    )
    args = ap.parse_args()

    grok_only = not bool(args.mixed_scenes)
    _apply_wanless_grok_upscale_profile(grok_only=grok_only)

    from shorts_pipeline.config.settings import Settings
    from shorts_pipeline.config.ui_store import effective_settings
    from shorts_pipeline.jobs.models import JobConfigSnapshot
    from shorts_pipeline.jobs.store import JobStore

    settings = effective_settings(Settings())
    store = JobStore(settings.data_dir / "jobs.sqlite")

    if not settings.grok_api_key:
        print("ERROR: SHORTS_GROK_API_KEY missing in .env", file=sys.stderr)
        return 1

    stages = [s.strip() for s in args.stages.split(",") if s.strip()]
    allowed = {"images", "tts", "align", "render"}
    bad = set(stages) - allowed
    if bad:
        print(f"ERROR: unknown stages: {sorted(bad)}", file=sys.stderr)
        return 1
    if not stages:
        print("ERROR: no stages selected", file=sys.stderr)
        return 1

    job_id = (args.job_id or "").strip() or None
    t0 = time.time()

    if job_id is None:
        if not args.plan:
            print("ERROR: provide --plan for a new job or --job-id to resume", file=sys.stderr)
            return 1
        plan_path = Path(args.plan)
        if not plan_path.is_file():
            print(f"ERROR: plan not found: {plan_path}", file=sys.stderr)
            return 1
        raw = json.loads(plan_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            print("ERROR: plan must be a JSON object", file=sys.stderr)
            return 1
        plan_json = _build_validated_plan(raw)
        bgm = _resolve_bgm(ROOT, settings)
        figure = str(plan_json.get("historical_figure") or "unknown").strip()
        job_id = store.create_job(
            JobConfigSnapshot(
                figure_name=figure,
                topic_type="historical_figure",
                language="en",
                bgm_path=str(bgm),
                watermark_enabled=False,
                end_plate_enabled=True,
                comfy_workflow_name=settings.comfy_workflow_name,
                overlay_enabled=True,
            )
        )
        out_plan = _write_and_register_plan(
            store=store,
            settings=settings,
            job_id=job_id,
            plan_json=plan_json,
        )
        print(f"[new-job] {job_id}", flush=True)
        print(f"[plan] {out_plan}", flush=True)
    else:
        if store.get_job(job_id) is None:
            print(f"ERROR: job {job_id} not in database", file=sys.stderr)
            return 1

    print(
        f"[profile] backend=smart_grok i2v=off upscale=on grok_only={grok_only} stages={stages}",
        flush=True,
    )
    _run_stages(
        store=store,
        settings=settings,
        job_id=job_id,
        stages=stages,
        preflight=bool(args.preflight),
    )
    print(f"[elapsed] {(time.time() - t0) / 60:.1f} min", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
