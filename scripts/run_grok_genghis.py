"""Run images (Grok smart_grok) + TTS + align + render for a Leonidas plan.

Skips the WAN I2V stage — uses still-image clauses only, exactly like the
older Henry-Ford-style pipeline.

Usage:
    # Create a new job from the embedded Leonidas plan:
    python scripts/run_grok_genghis.py

    # Or run against an existing job folder (must already have plan.json):
    python scripts/run_grok_genghis.py --job-id some-job-id

    # Or supply your own plan JSON file:
    python scripts/run_grok_genghis.py --plan path/to/plan.json
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

# Load .env so SHORTS_* (incl. SHORTS_GROK_API_KEY) reach the process.
_env = _ROOT / ".env"
if _env.is_file():
    for raw in _env.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key.startswith("SHORTS_"):
            os.environ[key] = val


_LEONIDAS_PLAN: dict = {
    "historical_figure": "Leonidas I",
    "cold_open_object": "A battered bronze Spartan helmet, half-buried in blood-soaked sand",
    "decision_lever": {
        "lever_type": "war",
        "description": "A king raised to believe fear was weakness, forced to choose between survival and immortal resistance against the largest army the world had ever seen.",
        "consequence": "Died at Thermopylae with his warriors, transforming a doomed defense into one of history's eternal symbols of sacrifice and defiance.",
    },
    "clauses": [
        {
            "text": "What kind of man walks knowingly toward death? The king tightened his grip.",
            "image_prompt": "Leonidas of Sparta, low-angle cinematic shot, standing beneath a storm-dark sky, battered crimson cape flowing violently in the wind, bronze Spartan helmet partially shadowing his face, spear gripped tightly in one hand, thousands of Spartan warriors blurred behind him, dramatic battlefield atmosphere, dust storms, cinematic realism, volumetric lighting, epic historical war film aesthetic.",
            "figure_present": True,
        },
        {
            "text": "From childhood, Sparta carved mercy out of him.",
            "image_prompt": "Young Spartan boy in brutal agoge training, standing barefoot in cold rain among older warriors, bruised face, shaved head, holding a wooden spear with trembling hands, harsh stone barracks in background, muted gray skies, raw historical realism, cinematic shadows, gritty texture.",
            "figure_present": True,
        },
        {
            "text": "Pain was education. Fear was disgrace.",
            "image_prompt": "Close-up of young Spartan warrior gripping a bloodied shield during combat training, dirt and sweat across his face, older Spartan instructor towering over him, harsh torchlight illuminating the scene, brutal ancient military realism, high-detail cinematic composition.",
            "figure_present": True,
        },
        {
            "text": "Then the Persian Empire came like a black tide.",
            "image_prompt": "Massive Persian army stretching endlessly across desert terrain, dark banners covering the horizon, armored cavalry, towering war elephants, thousands of soldiers marching beneath a burning orange sky, cinematic scale, apocalyptic atmosphere, historical epic realism.",
            "figure_present": False,
        },
        {
            "text": "Xerxes demanded submission. Earth. Water. Silence.",
            "image_prompt": "Persian messengers confronting Leonidas inside a massive Spartan stone hall, Leonidas seated on a throne of dark stone, expression cold and unreadable, firelight flickering across bronze armor, tense political confrontation, cinematic historical realism.",
            "figure_present": True,
        },
        {
            "text": "Leonidas chose war instead.",
            "image_prompt": "Leonidas rising from his throne, Spartan warriors behind him slamming spears into the ground, sparks and dust exploding upward, powerful golden firelight cutting through darkness, cinematic action freeze-frame, ultra dramatic composition.",
            "figure_present": True,
        },
        {
            "text": "Three hundred marched beside him. Not because they would survive. Because they would not.",
            "image_prompt": "Wide shot of 300 Spartans marching through a narrow mountain pass at dawn, shields and spears forming perfect symmetry, crimson cloaks flowing together, fog crawling across rocky terrain, ancient Greece cinematic realism, melancholy heroic atmosphere.",
            "figure_present": True,
        },
        {
            "text": "At Thermopylae, the world narrowed to steel, blood, and breath.",
            "image_prompt": "Violent close-quarters battle at Thermopylae, Spartans clashing against Persian soldiers inside the narrow pass, shields colliding, spears breaking, blood and dust suspended in the air, chaotic cinematic war photography, brutal realism, high contrast lighting.",
            "figure_present": True,
        },
        {
            "text": "Wave after wave crashed against them. The line never broke.",
            "image_prompt": "Leonidas leading Spartans behind locked shields as countless Persian soldiers charge toward them, arrows darkening the sky overhead, Spartan formation unshaken, cinematic battlefield scale, dramatic smoke and fire effects.",
            "figure_present": True,
        },
        {
            "text": "Then came betrayal.",
            "image_prompt": "A lone Greek traitor walking through a dark mountain path under moonlight, Persian soldiers following behind him carrying torches, ominous atmosphere, shadows swallowing the frame, suspenseful historical thriller aesthetic.",
            "figure_present": False,
        },
        {
            "text": "The Persians found the hidden path. Death closed in.",
            "image_prompt": "Leonidas turning toward distant fires appearing behind Spartan lines, realization visible in his eyes beneath the bronze helmet, smoke-filled battlefield at twilight, tragic cinematic realism, emotional tension.",
            "figure_present": True,
        },
        {
            "text": "Still, the Spartans remained.",
            "image_prompt": "Final stand of Spartans surrounded from every direction, broken shields, bloodied armor, exhausted warriors refusing to kneel, Leonidas standing at the center with spear raised, mythic cinematic composition, heroic despair.",
            "figure_present": True,
        },
        {
            "text": "A king died there. A legend replaced him.",
            "image_prompt": "Leonidas falling during battle in slow motion, spear slipping from his hand, sunlight piercing through battlefield smoke behind him, Spartan warriors fighting desperately around his body, epic tragic historical realism.",
            "figure_present": True,
        },
        {
            "text": "Empires fade. But defiance survives longer than fear.",
            "image_prompt": "Ancient ruined Spartan helmet resting alone at Thermopylae centuries later, golden sunset across abandoned battlefield, wind blowing dust through broken spears and shields, reflective cinematic atmosphere, timeless historical symbolism.",
            "figure_present": False,
        },
    ],
    "full_script": "What kind of man walks knowingly toward death? The king tightened his grip. From childhood, Sparta carved mercy out of him. Pain was education. Fear was disgrace. Then the Persian Empire came like a black tide. Xerxes demanded submission. Earth. Water. Silence. Leonidas chose war instead. Three hundred marched beside him. Not because they would survive. Because they would not. At Thermopylae, the world narrowed to steel, blood, and breath. Wave after wave crashed against them. The line never broke. Then came betrayal. The Persians found the hidden path. Death closed in. Still, the Spartans remained. A king died there. A legend replaced him. Empires fade. But defiance survives longer than fear.",
    "lut_choice": "dark_bronze",
    "end_plate_question": "If death was guaranteed, would you still stand and fight?",
}


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
    raise SystemExit("No BGM file found. Set SHORTS_DEFAULT_BGM_PATH or add assets/bgm.wav.")


def _ensure_env_defaults() -> None:
    # ComfyUI-free run for this script: raw Together/Grok PNGs, no /upload/image upscale.
    os.environ.setdefault("SHORTS_SKIP_COMFY_UPSCALE", "1")
    os.environ.setdefault("SHORTS_OUTRO_IMAGE_ENABLED", "0")
    os.environ.setdefault("SHORTS_I2V_ENABLED", "0")
    # Make ad-hoc JSON plans runnable without fighting strict validators.
    os.environ.setdefault("SHORTS_DEV_SKIP_PLAN_VALIDATION", "1")


def _compose_scene_prompt(clause_prompt: str) -> str:
    # Keep the user's prompt intact but enforce vertical framing + photo style.
    scene = (clause_prompt or "").strip()
    prefix = "Photorealistic vertical 9:16 cinematic still. Scene: "
    merged = prefix + scene
    return merged[:4000] if len(merged) > 4000 else merged


def _sanitize_grok_prompt(prompt: str) -> str:
    """Very small safety scrub for Grok 400s (violence/gore trigger words).

    We only touch high-frequency moderation triggers; the goal is to avoid hard
    failures while keeping the user's visual intent.
    """
    p = (prompt or "").strip()
    if not p:
        return p
    # Replace common gore terms with neutral equivalents.
    replacements = {
        "violent close-quarters battle": "intense shield-wall standoff in a narrow mountain pass",
        "close-quarters battle": "shield-wall standoff in a narrow pass",
        "clashing against": "facing",
        "blood and dust": "dust and smoke",
        "spears breaking": "spears lowered",
        "falling during battle": "kneeling at dusk, head bowed, spear beside him",
        "fighting desperately around his body": "standing in smoky formation behind him",
        "final stand of spartans surrounded": "Spartan warriors in a tight shield formation on a cliff",
        "bloodied armor": "weathered bronze armor",
        "broken shields": "scarred shields",
        "exhausted warriors refusing to kneel": "weary warriors standing tall",
        "blood-soaked": "dust-soaked",
        "blood soaked": "dust soaked",
        "bloodied": "battle-worn",
        "blood": "dust",
        "brutal": "intense",
        "violent": "dramatic",
        "gore": "ruin",
        "guts": "wreckage",
        "severed": "broken",
        "decapitated": "defeated",
        "dismembered": "shattered",
        "dead body": "fallen warrior",
        "corpse": "fallen warrior",
        # Child-related moderation triggers (avoid depicting child harm).
        "young spartan boy": "young Spartan trainee",
        "little boy": "young trainee",
        "child": "youth",
        "boy": "trainee",
        "brutal": "intense",
        "bruised face": "determined face",
        "bloodied shield": "battered shield",
    }
    lowered = p.lower()
    for k, v in replacements.items():
        if k in lowered:
            # case-insensitive-ish replace: do a simple lower pass then rebuild
            p = p.replace(k, v).replace(k.title(), v).replace(k.upper(), v)
            lowered = p.lower()
    return p


def _looks_like_child_scene(image_prompt: str) -> bool:
    s = (image_prompt or "").lower()
    return any(tok in s for tok in ("young boy", "boy", "child", "teen", "kid"))


def _safe_image_prompt_for_grok(image_prompt: str) -> str:
    """If a prompt implies child harm, rewrite to an adult-safe equivalent."""
    p = (image_prompt or "").strip()
    if not p:
        return p
    if not _looks_like_child_scene(p):
        return p
    # Keep the gist (Spartan training in harsh weather) but remove youth framing.
    return (
        "Adult Spartan trainee in intense agoge-style training, medium shot, standing in cold rain, "
        "wet cloak and leather armor, determined expression, gripping a wooden spear, "
        "stone barracks in the background, cinematic shadows, gritty historical realism, torchlight glow."
    )


def _generate_together_flux_only_for_job(
    *,
    settings,
    store,
    job_id: str,
    plan_path: Path,
    skip_existing: bool = True,
) -> None:
    """Generate every clause image via Together FLUX.2 Pro — no Grok, no Comfy."""
    import json

    from shorts_pipeline.image_worker.flux_api_client import FluxApiError, TogetherFluxImageClient
    from shorts_pipeline.jobs.models import ArtifactType, PipelineStage

    if not settings.flux_api_key:
        raise SystemExit("SHORTS_FLUX_API_KEY missing — set Together key in .env")
    provider = settings.flux_api_provider.lower().strip()
    if provider not in ("together", "together_ai", "together-ai"):
        raise SystemExit(
            f"SHORTS_FLUX_API_PROVIDER must be 'together' for FLUX 2 Pro (got {provider!r})"
        )

    data = json.loads(plan_path.read_text(encoding="utf-8"))
    clauses = data.get("clauses") if isinstance(data, dict) else None
    if not isinstance(clauses, list) or not clauses:
        raise RuntimeError("plan.json missing clauses[]")

    together = TogetherFluxImageClient(settings)
    out_dir = (settings.data_dir / "jobs" / job_id / "images").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = out_dir / "_pro_raw"
    raw_dir.mkdir(exist_ok=True)

    total = len(clauses)
    for i, c in enumerate(clauses):
        if not isinstance(c, dict):
            raise RuntimeError(f"clause {i} is not an object")
        img_prompt = str(c.get("image_prompt") or "").strip()
        if not img_prompt:
            raise RuntimeError(f"clause {i} missing image_prompt")

        out_path = out_dir / f"clause_{i:03d}.png"
        if skip_existing and out_path.is_file() and out_path.stat().st_size > 32:
            print(f"[flux] skip existing clause_{i:03d}.png")
            store.add_artifact(
                job_id,
                PipelineStage.images,
                ArtifactType.image_png,
                out_path,
                meta={"source": "together_flux2_pro", "skipped": True},
            )
            continue

        safe = _safe_image_prompt_for_grok(img_prompt)
        prompt = _sanitize_grok_prompt(_compose_scene_prompt(safe))
        print(f"[flux] clause {i + 1}/{total} model={settings.flux_api_model}")
        try:
            png = together.generate(prompt)
        except FluxApiError as exc:
            print(f"[flux] ERROR clause={i + 1} {exc}", file=sys.stderr)
            if exc.detail:
                print(f"[flux] detail: {str(exc.detail)[:800]}", file=sys.stderr)
            raise

        raw_path = raw_dir / f"clause_{i:03d}.png"
        raw_path.write_bytes(png)
        out_path.write_bytes(png)
        store.add_artifact(
            job_id,
            PipelineStage.images,
            ArtifactType.image_png,
            out_path,
            meta={"source": "together_flux2_pro", "model": settings.flux_api_model},
        )
        print(f"[flux] saved {out_path.name} ({len(png) // 1024} KB)")


def _generate_raw_grok_images_for_job(
    *,
    settings,
    store,
    job_id: str,
    plan_path: Path,
) -> None:
    import json

    from shorts_pipeline.image_worker.grok_bookend_generator import compose_grok_prompt
    from shorts_pipeline.image_worker.grok_image_client import GrokImageError
    from shorts_pipeline.image_worker.grok_image_client import build_grok_image_client
    from shorts_pipeline.jobs.models import ArtifactType, PipelineStage

    data = json.loads(plan_path.read_text(encoding="utf-8"))
    clauses = data.get("clauses") if isinstance(data, dict) else None
    if not isinstance(clauses, list) or not clauses:
        raise RuntimeError("plan.json missing clauses[]")

    figure_name = (data.get("historical_figure") if isinstance(data, dict) else "") or ""
    figure_name = str(figure_name).strip() or None

    out_dir = (settings.data_dir / "jobs" / job_id / "images").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    grok = build_grok_image_client(settings)

    for i, c in enumerate(clauses):
        if not isinstance(c, dict):
            raise RuntimeError(f"clause {i} is not an object")
        img_prompt = str(c.get("image_prompt") or "").strip()
        if not img_prompt:
            raise RuntimeError(f"clause {i} missing image_prompt")
        img_prompt_for_grok = _safe_image_prompt_for_grok(img_prompt)
        figure_present = bool(c.get("figure_present", True))
        # Safety: if the prompt depicts a child/youth, avoid "consistent likeness"
        # framing and prefer a neutral scene prompt.
        if _looks_like_child_scene(img_prompt):
            figure_present = False
        prompt_raw = (
            compose_grok_prompt(figure_name, img_prompt_for_grok)
            if figure_present
            else _compose_scene_prompt(img_prompt_for_grok)
        )
        prompt = _sanitize_grok_prompt(prompt_raw)
        out_path = out_dir / f"clause_{i:03d}.png"
        try:
            data_png = grok.generate(prompt)
        except GrokImageError as exc:
            # Print details to make debugging actionable.
            print(
                f"[grok] ERROR clause={i+1}/{len(clauses)} figure_present={figure_present} http={exc.status_code}",
                file=sys.stderr,
            )
            if exc.detail:
                print(f"[grok] detail: {str(exc.detail)[:1200]}", file=sys.stderr)
            print(f"[grok] prompt_preview: {prompt[:240]}", file=sys.stderr)
            raise
        out_path.write_bytes(data_png)
        store.add_artifact(
            job_id,
            PipelineStage.images,
            ArtifactType.image_png,
            out_path,
            meta={"source": "grok_raw", "figure_present": figure_present},
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-id", default=None, help="Existing job id (folder under data/jobs/)")
    parser.add_argument("--plan", default=None, help="Path to a plan.json file to use for a NEW job")
    parser.add_argument(
        "--stages",
        default="images,tts,align,render",
        help="Comma-separated subset of: images,tts,align,render",
    )
    parser.add_argument(
        "--raw-grok-only",
        action="store_true",
        help="Generate ALL clause images via Grok API (no Comfy upscaling / no Flux).",
    )
    parser.add_argument(
        "--together-only",
        action="store_true",
        help="Generate ALL clause images via Together FLUX.2 Pro (no Grok, no Comfy).",
    )
    parser.add_argument(
        "--force-images",
        action="store_true",
        help="With --together-only: regenerate even if clause_*.png already exists.",
    )
    args = parser.parse_args()

    from shorts_pipeline.config.settings import Settings
    from shorts_pipeline.jobs.models import ArtifactType, JobConfigSnapshot, PipelineStage
    from shorts_pipeline.jobs.store import JobStore
    from shorts_pipeline.orchestrator import PipelineOrchestrator

    _ensure_env_defaults()
    settings = Settings()
    store = JobStore(settings.data_dir / "jobs.sqlite")

    job_id = (args.job_id or "").strip() or None
    if job_id is None:
        import json

        # Create a NEW job and write plan.json into its folder.
        plan_obj = _LEONIDAS_PLAN
        if args.plan:
            plan_obj = json.loads(Path(args.plan).read_text(encoding="utf-8"))
        if not isinstance(plan_obj, dict):
            print("ERROR: plan must be a JSON object", file=sys.stderr)
            return 1

        bgm = _resolve_bgm(_ROOT, settings)
        figure = (plan_obj.get("historical_figure") or "unknown").strip()
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
        job_dir = settings.data_dir / "jobs" / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        plan_path = job_dir / "plan.json"
        plan_path.write_text(json.dumps(plan_obj, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[new-job] {job_id} -> {plan_path}")
    else:
        rec = store.get_job(job_id)
        if rec is None:
            print(f"ERROR: job {job_id} not in database.", file=sys.stderr)
            return 1
        job_dir = settings.data_dir / "jobs" / job_id
        plan_path = job_dir / "plan.json"
        if not plan_path.is_file():
            print(f"ERROR: missing {plan_path}", file=sys.stderr)
            return 1

    # Ensure plan artifact registered so subsequent stages find it.
    store.add_artifact(
        job_id,
        PipelineStage.plan,
        ArtifactType.plan_json,
        plan_path,
        meta={"source": "run_grok_plan"},
    )
    store.update_job_progress(
        job_id,
        last_completed_stage=PipelineStage.plan,
        clear_error=True,
    )

    orch = PipelineOrchestrator(settings, store)
    stages = [s.strip() for s in args.stages.split(",") if s.strip()]

    print(f"[run] job={job_id} backend={settings.image_backend} stages={stages}")

    if args.raw_grok_only and args.together_only:
        print("ERROR: use only one of --raw-grok-only or --together-only", file=sys.stderr)
        return 1

    if "images" in stages:
        print("[images] start")
        if args.together_only:
            _generate_together_flux_only_for_job(
                settings=settings,
                store=store,
                job_id=job_id,
                plan_path=plan_path,
                skip_existing=not args.force_images,
            )
        elif args.raw_grok_only:
            _generate_raw_grok_images_for_job(
                settings=settings,
                store=store,
                job_id=job_id,
                plan_path=plan_path,
            )
        else:
            orch.run_images(job_id)
        store.update_job_progress(job_id, last_completed_stage=PipelineStage.images)
        print("[images] done")

    if "tts" in stages:
        print("[tts] start")
        orch.run_tts(job_id)
        store.update_job_progress(job_id, last_completed_stage=PipelineStage.tts)
        print("[tts] done")

    if "align" in stages:
        print("[align] start")
        orch.run_align(job_id)
        store.update_job_progress(job_id, last_completed_stage=PipelineStage.align)
        print("[align] done")

    if "render" in stages:
        print("[render] start (i2v skipped — still-image clauses)")
        orch.run_render(job_id)
        store.update_job_progress(job_id, last_completed_stage=PipelineStage.render)
        print("[render] done")

    final_mp4 = job_dir / "final.mp4"
    if final_mp4.is_file():
        print(f"[ok] {final_mp4}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
