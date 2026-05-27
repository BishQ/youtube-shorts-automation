"""Create a job from an inline Leonidas plan JSON and render final videos.

This script bypasses the planner (LLM) stage: it writes a validated plan.json
into a new job folder, registers it in jobs.sqlite, and runs the pipeline only
up to the render stage (no publish).

Outputs are written under: data/jobs/<job_id>/
  - images/clause_*.png
  - narration.wav
  - clause_timings.json
  - subtitles.ass
  - final.mp4
  - final_long.mp4 (if enabled in Settings)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from shorts_pipeline.config.settings import Settings  # noqa: E402
from shorts_pipeline.config.ui_store import effective_settings  # noqa: E402
from shorts_pipeline.context import set_job_id  # noqa: E402
from shorts_pipeline.jobs.concurrency import PipelineProcessLock  # noqa: E402
from shorts_pipeline.jobs.models import JobConfigSnapshot, JobStatus, PipelineStage, ArtifactType  # noqa: E402
from shorts_pipeline.jobs.preflight import require_preflight  # noqa: E402
from shorts_pipeline.jobs.store import JobStore  # noqa: E402
from shorts_pipeline.logging_setup import get_logger  # noqa: E402
from shorts_pipeline.orchestrator import PipelineOrchestrator  # noqa: E402
from shorts_pipeline.planner.schema import NarrationPlan  # noqa: E402

log = get_logger(__name__)


# The user-provided JSON (kept close to the prompt but normalized for schema + safety).
_RAW_LEONIDAS_PLAN: dict[str, Any] = {
    "historical_figure": "Leonidas I",
    "cold_open_object": "A battered bronze Spartan helmet, half-buried in sand",
    "decision_lever": {
        # Schema enum is limited; map "war" -> "politics" (power/sovereignty decision).
        "lever_type": "politics",
        "description": "A king raised to believe fear was weakness, forced to choose between survival and immortal resistance against the largest army the world had ever seen.",
        "consequence": "Died at Thermopylae with his warriors, transforming a doomed defense into one of history's eternal symbols of sacrifice and defiance.",
    },
    "clauses": [
        {
            "text": "What kind of man walks to death on purpose, while his men watch? The king gripped his spear and did not flinch.",
            "image_prompt": "Leonidas of Sparta, low-angle cinematic shot, standing beneath a storm-dark sky, battered crimson cape flowing violently in the wind, bronze Spartan helmet partially shadowing his face, spear gripped tightly in one hand, thousands of Spartan warriors blurred behind him, dramatic battlefield atmosphere, dust storms, cinematic realism, volumetric lighting, epic historical war film aesthetic.",
            "figure_present": True,
        },
        {
            "text": "As a boy, Sparta cut softness from him, and taught his bones to obey.",
            "image_prompt": "Young Spartan boy in brutal agoge training, medium shot, standing barefoot in cold rain among older warriors, bruised face, shaved head, holding a wooden spear with trembling hands, harsh stone barracks in background, muted gray skies, raw historical realism, cinematic shadows, gritty texture.",
            "figure_present": True,
        },
        {
            "text": "Pain was class. Hunger was normal. Fear was shame, and weakness got punished.",
            "image_prompt": "Close-up of young Spartan warrior gripping a battered shield during combat training, dirt and sweat across his face, older Spartan instructor towering over him, harsh torchlight illuminating the scene, brutal ancient military realism, high-detail cinematic composition.",
            "figure_present": True,
        },
        {
            "text": "Then Persia came like a dark wave, taking towns as if they were sand.",
            "image_prompt": "Massive Persian army stretching endlessly across desert terrain, establishing wide shot, dark banners covering the horizon, armored cavalry, towering war elephants, thousands of soldiers marching beneath a burning orange sky, harsh sunlight slicing through dust haze, cinematic scale, apocalyptic atmosphere, historical epic realism.",
            "figure_present": False,
        },
        {
            "text": "Xerxes asked for earth and water: a sign that Sparta would kneel.",
            "image_prompt": "Persian messengers confronting Leonidas inside a massive Spartan stone hall, medium shot, Leonidas seated on a throne of dark stone, expression cold and unreadable, firelight flickering across bronze armor, tense political confrontation, cinematic historical realism.",
            "figure_present": True,
        },
        {
            "text": "Leonidas chose war, because bowing would stain his name forever.",
            "image_prompt": "Leonidas rising from his throne, hero shot, Spartan warriors behind him slamming spears into the ground, sparks and dust exploding upward, powerful golden firelight cutting through darkness, cinematic action freeze-frame, ultra dramatic composition.",
            "figure_present": True,
        },
        {
            "text": "Three hundred went with him, not to live, but to stand and hold.",
            "image_prompt": "Wide shot of 300 Spartans marching through a narrow mountain pass at dawn, shields and spears forming perfect symmetry, crimson cloaks flowing together, fog crawling across rocky terrain, ancient Greece cinematic realism, melancholy heroic atmosphere, soft dawn light.",
            "figure_present": True,
        },
        {
            "text": "At Thermopylae the world shrank to shield, spear, dust, and breath.",
            "image_prompt": "Violent close-quarters battle at Thermopylae, medium shot, Spartans clashing against Persian soldiers inside the narrow pass, shields colliding, spears breaking, dust suspended in the air, chaotic cinematic war photography, brutal realism, high contrast lighting.",
            "figure_present": True,
        },
        {
            "text": "Wave after wave hit them, and the line held, tight and unbroken.",
            "image_prompt": "Leonidas leading Spartans behind locked shields as countless Persian soldiers charge toward them, wide shot, arrows darkening the sky overhead, Spartan formation unshaken, cinematic battlefield scale, dramatic smoke and firelight effects.",
            "figure_present": True,
        },
        {
            "text": "Then a traitor spoke, and the hills gave up a hidden track.",
            "image_prompt": "A lone Greek traitor walking through a dark mountain path under moonlight, medium shot, Persian soldiers following behind him carrying torches, ominous atmosphere, shadows swallowing the frame, suspenseful historical thriller aesthetic.",
            "figure_present": False,
        },
        {
            "text": "The foe found the pass, and death closed in from front and rear.",
            "image_prompt": "Leonidas turning toward distant fires appearing behind Spartan lines, close-up, realization visible in his eyes beneath the bronze helmet, smoke-filled battlefield at twilight, tragic cinematic realism, emotional tension, low orange firelight.",
            "figure_present": True,
        },
        {
            "text": "Still they stayed, and picked a last stand over a long life of what if.",
            "image_prompt": "Final stand of Spartans surrounded from every direction, wide shot, broken shields, battered armor, exhausted warriors refusing to kneel, Leonidas standing at the center with spear raised, mythic cinematic composition, heroic despair, stormy backlight.",
            "figure_present": True,
        },
        {
            "text": "A king fell there, and a tale rose up that shook the world.",
            "image_prompt": "Leonidas falling during battle in slow motion, medium shot, spear slipping from his hand, sunlight piercing through battlefield smoke behind him, Spartan warriors fighting desperately around him, epic tragic historical realism, volumetric sunlight.",
            "figure_present": True,
        },
        {
            "text": "Empires fade, but brave defiance lasts longer than fear, in every age.",
            "image_prompt": "Ancient ruined Spartan helmet resting alone at Thermopylae centuries later, establishing wide shot, golden sunset across an abandoned battlefield, wind blowing dust through broken spears and shields, reflective cinematic atmosphere, timeless historical symbolism, warm sunset light.",
            "figure_present": False,
        },
    ],
    # Note: full_script is recomputed from clause texts at runtime for perfect alignment.
    "full_script": "",
    # Schema enum: pick the closest grade.
    "lut_choice": "dark_thriller",
    "end_plate_question": "If death was guaranteed, would you still stand and fight?",
}


def _resolve_bgm(root: Path, settings: Settings) -> Path:
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
    raise SystemExit("No BGM file found. Set SHORTS_DEFAULT_BGM_PATH or add assets/bgm.wav.")


def _beat_for_index(i: int, total: int) -> dict[str, Any]:
    # Conservative defaults that satisfy schema and produce a decent pacing curve.
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
    # Mid-body curve: alternate motion a bit to avoid a static slideshow feel.
    camera_cycle = ["ken_burns", "pan", "zoom_out", "hold", "parallax"]
    emotion = "tense_buildup" if i < (total * 0.45) else "suspense" if i < (total * 0.75) else "climactic"
    return {
        "emotion": emotion,
        "intensity": 0.72 if emotion != "climactic" else 0.9,
        "camera": camera_cycle[i % len(camera_cycle)],
        "transition_in": "xfade",
        "duration_hint": "medium",
        "color_grade": "dark_thriller" if i % 2 else "tragic_cold",
        "audio_event": "sword_clash" if 6 <= i <= 9 else "none",
        "visual_tier": "cinematic",
        "subtitle_position": "bottom",
        "cut_target": None,
    }


def _build_validated_plan(raw: dict[str, Any]) -> dict[str, Any]:
    # Add beat blocks, ensure required fields exist, and run full schema validation.
    clauses_in = raw.get("clauses") if isinstance(raw.get("clauses"), list) else []
    clauses: list[dict[str, Any]] = []
    clause_texts: list[str] = []
    for idx, c in enumerate(clauses_in):
        if not isinstance(c, dict):
            continue
        txt = str(c.get("text") or "").strip()
        clause_texts.append(txt)
        clauses.append(
            {
                "text": txt,
                "image_prompt": str(c.get("image_prompt") or "").strip(),
                "beat": _beat_for_index(idx, len(clauses_in)),
                "figure_present": bool(c.get("figure_present", True)),
            }
        )

    # Force full_script to match clause texts exactly (aligner uses full_script).
    full_script = " ".join(t for t in clause_texts if t).strip()

    plan_obj: dict[str, Any] = {
        "historical_figure": str(raw.get("historical_figure") or "").strip(),
        "cold_open_object": str(raw.get("cold_open_object") or "").strip(),
        "decision_lever": raw.get("decision_lever") if isinstance(raw.get("decision_lever"), dict) else {},
        "clauses": clauses,
        "full_script": full_script,
        "lut_choice": str(raw.get("lut_choice") or "dark_thriller").strip(),
        "end_plate_question": str(raw.get("end_plate_question") or "").strip(),
    }

    # Full validation (word/safety/enum gates).
    validated = NarrationPlan.model_validate(plan_obj, context={"allow_figure_name": True})
    return validated.model_dump(mode="json")


def _write_and_register_plan(
    *,
    store: JobStore,
    settings: Settings,
    job_id: str,
    plan_json: dict[str, Any],
) -> Path:
    jd = (settings.data_dir / "jobs" / job_id).resolve()
    jd.mkdir(parents=True, exist_ok=True)
    path = jd / "plan.json"
    path.write_text(json.dumps(plan_json, ensure_ascii=False, indent=2), encoding="utf-8")
    store.add_artifact(job_id, PipelineStage.plan, ArtifactType.plan_json, path, meta={"source": "inline_json"})
    # Make sure the job is positioned to resume from images.
    store.update_job_progress(
        job_id,
        status=JobStatus.pending,
        current_stage=PipelineStage.images,
        last_completed_stage=PipelineStage.plan,
        clear_error=True,
    )
    return path


def _run_to_render(
    *,
    store: JobStore,
    settings: Settings,
    job_id: str,
    preflight: bool,
    i2v_enabled: bool,
) -> None:
    set_job_id(job_id)
    if preflight:
        require_preflight(settings)

    orch = PipelineOrchestrator(settings, store)
    store.update_job_progress(job_id, status=JobStatus.running, current_stage=PipelineStage.images, clear_error=True)

    orch.run_images(job_id)
    store.update_job_progress(job_id, last_completed_stage=PipelineStage.images, current_stage=PipelineStage.tts)

    orch.run_tts(job_id)
    store.update_job_progress(job_id, last_completed_stage=PipelineStage.tts, current_stage=PipelineStage.align)

    orch.run_align(job_id)
    store.update_job_progress(job_id, last_completed_stage=PipelineStage.align, current_stage=PipelineStage.i2v)

    if i2v_enabled and settings.i2v_enabled:
        orch.run_i2v(job_id)
    store.update_job_progress(job_id, last_completed_stage=PipelineStage.i2v, current_stage=PipelineStage.render)

    orch.run_render(job_id)
    store.update_job_progress(
        job_id,
        status=JobStatus.completed,
        last_completed_stage=PipelineStage.render,
        clear_current_stage=True,
        clear_error=True,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--plan",
        default=None,
        help="Optional path to a plan JSON file. If omitted, uses the embedded Leonidas plan.",
    )
    ap.add_argument("--preflight", action="store_true", help="Run environment preflight checks first.")
    ap.add_argument("--no-i2v", action="store_true", help="Skip Wan I2V clips even if enabled in Settings.")
    args = ap.parse_args()

    base = Settings()
    settings = effective_settings(base)

    # Build a validated NarrationPlan JSON (schema-safe).
    if args.plan:
        raw = json.loads(Path(args.plan).read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise SystemExit("--plan must be a JSON object")
    else:
        raw = dict(_RAW_LEONIDAS_PLAN)
    plan_json = _build_validated_plan(raw)

    bgm = _resolve_bgm(ROOT, settings)
    store = JobStore(settings.data_dir / "jobs.sqlite")

    job_id = store.create_job(
        JobConfigSnapshot(
            figure_name=plan_json["historical_figure"],
            topic_type="historical_figure",
            language="en",
            bgm_path=str(bgm),
            watermark_enabled=False,
            end_plate_enabled=True,
            comfy_workflow_name=settings.comfy_workflow_name,
            overlay_enabled=True,
        )
    )

    t0 = time.time()
    plan_path = _write_and_register_plan(store=store, settings=settings, job_id=job_id, plan_json=plan_json)
    log.info("inline_plan_written", job_id=job_id, path=str(plan_path))

    with PipelineProcessLock(settings.data_dir):
        _run_to_render(
            store=store,
            settings=settings,
            job_id=job_id,
            preflight=bool(args.preflight),
            i2v_enabled=not bool(args.no_i2v),
        )

    jd = (settings.data_dir / "jobs" / job_id).resolve()
    elapsed = time.time() - t0
    print(f"job_id: {job_id}")
    print(f"job_dir: {jd}")
    print(f"final: {jd / 'final.mp4'}")
    print(f"final_long: {jd / 'final_long.mp4'}")
    print(f"elapsed_s: {elapsed:.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

