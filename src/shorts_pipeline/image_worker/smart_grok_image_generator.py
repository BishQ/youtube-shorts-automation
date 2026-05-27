"""3-tier smart image router:

  figure_present=True  → Grok Imagine  (real person, likeness accuracy)
  figure_present=False → Together AI Flux.2 Pro  (pro-grade epic scenes)
  Together not configured → ComfyUI local Flux (silent fallback)
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable

from shorts_pipeline.config.settings import Settings
from shorts_pipeline.image_worker.comfy import ComfyClient, load_upscale_bundle, load_workflow_bundle
from shorts_pipeline.image_worker.flux_api_client import FluxApiError, TogetherFluxImageClient
from shorts_pipeline.image_worker.grok_bookend_generator import compose_grok_prompt
from shorts_pipeline.image_worker.grok_image_client import build_grok_image_client
from shorts_pipeline.logging_setup import get_logger

log = get_logger(__name__)


def _together_available(settings: Settings) -> bool:
    # Hard kill-switch via env var — set SHORTS_FLUX_DISABLED=true to force
    # all scene clauses through Grok (or local Qwen fallback) without editing
    # the .env Flux key.
    import os
    if (os.environ.get("SHORTS_FLUX_DISABLED", "") or "").strip().lower() in ("1", "true", "yes", "on"):
        return False
    return bool(
        settings.flux_api_key
        and settings.flux_api_provider.lower().strip() in ("together", "together_ai", "together-ai")
    )


class SmartGrokImageGenerator:
    """Route per clause: figure → Grok, scene → Together Flux 2 Pro (or local fallback)."""

    def __init__(self, settings: Settings) -> None:
        self._s = settings

    def _finalize_api_png(
        self,
        raw_path: Path,
        out_path: Path,
        *,
        comfy: ComfyClient,
        up_bundle: object,
        clause: int,
        source: str,
    ) -> None:
        if self._s.skip_comfy_upscale:
            shutil.copyfile(raw_path, out_path)
            log.info(
                "smart_grok_skip_upscale",
                clause=clause,
                source=source,
                out=out_path.name,
            )
            return
        if comfy is None or up_bundle is None:
            raise RuntimeError("Comfy upscale required but Comfy client was not initialized")
        log.info(f"smart_grok_{source}_upscale", clause=clause)
        comfy.upscale_image(up_bundle, raw_path, out_path)

    def generate_all(
        self,
        prompts: list[str],
        figure_present_flags: list[bool],
        out_dir: Path,
        *,
        figure_display_name: str | None = None,
        progress_cb: Callable[[int, str], None] | None = None,
    ) -> list[Path]:
        if not prompts:
            return []

        out_dir.mkdir(parents=True, exist_ok=True)
        total = len(prompts)

        flags = list(figure_present_flags)
        while len(flags) < total:
            flags.append(True)

        # Hard kill-switch: SHORTS_GROK_ONLY=true sends EVERY clause to Grok
        # (no scene split to Flux/Comfy). Use when Flux is paused and you want
        # consistent Grok visuals for the whole documentary.
        import os
        grok_only = (os.environ.get("SHORTS_GROK_ONLY", "") or "").strip().lower() in ("1", "true", "yes", "on")
        if grok_only:
            grok_idx = set(range(total))
            scene_idx = set()
        else:
            grok_idx = {i for i, f in enumerate(flags) if f}
            scene_idx = set(range(total)) - grok_idx

        use_together = _together_available(self._s)
        scene_backend = "together" if use_together else "comfy"

        skip_up = bool(self._s.skip_comfy_upscale)

        log.info(
            "smart_grok_generate_start",
            total=total,
            grok_clauses=sorted(i + 1 for i in grok_idx),
            scene_clauses=sorted(i + 1 for i in scene_idx),
            scene_backend=scene_backend,
            skip_comfy_upscale=skip_up,
            figure=figure_display_name or "",
        )

        # Only spin up Comfy when we must generate locally or upscale via workflow.
        need_local_scene_gen = (not use_together) and bool(scene_idx)
        need_upscale = (not skip_up) and (bool(grok_idx) or (use_together and bool(scene_idx)))
        need_comfy = need_local_scene_gen or need_upscale
        comfy: ComfyClient | None = None
        gen_bundle = None
        up_bundle = None
        if need_comfy:
            wf_path = (self._s.workflows_dir / f"{self._s.comfy_workflow_name}.json").resolve()
            gen_bundle = load_workflow_bundle(wf_path)
            up_path = (
                self._s.workflows_dir / f"{self._s.comfy_upscale_workflow_name}.json"
            ).resolve()
            up_bundle = load_upscale_bundle(up_path)
            comfy = ComfyClient(self._s)

        grok = build_grok_image_client(self._s) if grok_idx else None
        together = TogetherFluxImageClient(self._s) if use_together else None

        grok_raw_dir = out_dir / "_grok_raw"
        grok_raw_dir.mkdir(exist_ok=True)
        pro_raw_dir = out_dir / "_pro_raw"
        if together:
            pro_raw_dir.mkdir(exist_ok=True)

        final: dict[int, Path] = {}

        # ── Scene clauses (no figure) ────────────────────────────────────────
        for i in sorted(scene_idx):
            out_path = out_dir / f"clause_{i:03d}.png"
            if together:
                raw_path = pro_raw_dir / f"clause_{i:03d}.png"
                log.info("smart_grok_pro_scene", clause=i + 1, total=total,
                         model=self._s.flux_api_model, prompt_preview=prompts[i][:80])
                try:
                    data = together.generate(prompts[i])
                    raw_path.write_bytes(data)
                    self._finalize_api_png(
                        raw_path,
                        out_path,
                        comfy=comfy,
                        up_bundle=up_bundle,
                        clause=i + 1,
                        source="pro_scene",
                    )
                    final[i] = out_path
                except (FluxApiError, OSError) as exc:
                    log.warning("smart_grok_pro_scene_failed_fallback", clause=i + 1,
                                error=str(exc)[:200])
                    if skip_up:
                        raise RuntimeError(
                            f"Together failed for clause {i + 1} and "
                            "SHORTS_SKIP_COMFY_UPSCALE=1 (Comfy fallback disabled)"
                        ) from exc
                    assert comfy is not None and gen_bundle is not None
                    comfy.generate_one(gen_bundle, prompts[i], out_path)
                    final[i] = out_path
            else:
                if skip_up:
                    raise RuntimeError(
                        f"clause {i + 1} needs Comfy (no Together, skip_comfy_upscale=True)"
                    )
                log.info("smart_grok_local_scene", clause=i + 1, total=total,
                         prompt_preview=prompts[i][:80])
                assert comfy is not None and gen_bundle is not None
                comfy.generate_one(gen_bundle, prompts[i], out_path)
                final[i] = out_path
            if progress_cb:
                progress_cb(i, prompts[i])

        # ── Figure clauses — Grok generate then upscale ──────────────────────
        for i in sorted(grok_idx):
            grok_prompt = compose_grok_prompt(figure_display_name, prompts[i])
            raw_path = grok_raw_dir / f"clause_{i:03d}.png"
            log.info("smart_grok_figure", clause=i + 1, total=total,
                     prompt_preview=grok_prompt[:120])
            data = grok.generate(grok_prompt)
            raw_path.write_bytes(data)

        for i in sorted(grok_idx):
            raw_path = grok_raw_dir / f"clause_{i:03d}.png"
            out_path = out_dir / f"clause_{i:03d}.png"
            self._finalize_api_png(
                raw_path,
                out_path,
                comfy=comfy,
                up_bundle=up_bundle,
                clause=i + 1,
                source="figure",
            )
            final[i] = out_path
            if progress_cb:
                progress_cb(i, prompts[i])

        missing = set(range(total)) - set(final.keys())
        if missing:
            raise RuntimeError(f"SmartGrok incomplete — missing clause indices: {sorted(missing)}")

        log.info(
            "smart_grok_generate_done",
            total=total,
            grok_count=len(grok_idx),
            scene_count=len(scene_idx),
            scene_backend=scene_backend,
        )
        return [final[i] for i in range(total)]
