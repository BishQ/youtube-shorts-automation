"""Three-way image routing: Grok Imagine + Flux API + local ComfyUI.

Uses the same Flux ``api_indices`` rule as ``hybrid`` (evens + last, except
second-to-last is local). Indices that Grok takes (first, last, optional
extras) are removed from the Flux set so each clause uses exactly one remote
or local path.

Pipeline:
  1. Flux API → ``_flux_raw/clause_NNN.png`` for flux slots.
  2. Grok Imagine → ``_grok_raw/clause_NNN.png`` for grok slots.
  3. Local ComfyUI → final ``clause_NNN.png`` for local slots.
  4. Upscale every Flux and Grok raw via the same Comfy upscale workflow.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from shorts_pipeline.config.settings import Settings
from shorts_pipeline.image_worker.comfy import (
    ComfyClient,
    load_upscale_bundle,
    load_workflow_bundle,
)
from shorts_pipeline.image_worker.flux_api_client import build_flux_api_client
from shorts_pipeline.image_worker.grok_bookend_generator import (
    compose_grok_prompt,
    grok_clause_indices,
)
from shorts_pipeline.image_worker.grok_image_client import build_grok_image_client
from shorts_pipeline.image_worker.hybrid_image_generator import api_indices
from shorts_pipeline.logging_setup import get_logger

log = get_logger(__name__)


def split_grok_flux_local_indices(
    total: int,
    grok_extra_clause_indices: str | None,
) -> tuple[set[int], set[int], set[int]]:
    """Return (grok_indices, flux_api_indices, local_indices), disjoint, union = range(total)."""
    if total <= 0:
        return set(), set(), set()
    grok = grok_clause_indices(total, grok_extra_clause_indices)
    flux = api_indices(total) - grok
    local = set(range(total)) - grok - flux
    return grok, flux, local


class TripleHybridImageGenerator:
    """Grok bookends (+ extras) + Flux API for remaining hybrid slots + Comfy local."""

    def __init__(self, settings: Settings) -> None:
        self._s = settings

    @staticmethod
    def _final_png_ready(comfy: ComfyClient, path: Path, *, min_w: int, min_h: int) -> bool:
        if not path.is_file():
            return False
        try:
            comfy.verify_png(path, min_w=min_w, min_h=min_h)
        except Exception:
            return False
        return True

    def generate_all(
        self,
        prompts: list[str],
        out_dir: Path,
        *,
        figure_display_name: str | None = None,
        progress_cb: Callable[[int, str], None] | None = None,
        resume: bool = False,
    ) -> list[Path]:
        if not prompts:
            return []

        out_dir.mkdir(parents=True, exist_ok=True)
        total = len(prompts)
        grok_idx, flux_idx, local_idx = split_grok_flux_local_indices(
            total,
            self._s.grok_extra_clause_indices,
        )

        log.info(
            "triple_hybrid_generate_start",
            total=total,
            grok_clauses=sorted(i + 1 for i in grok_idx),
            flux_clauses=sorted(i + 1 for i in flux_idx),
            local_clauses=sorted(i + 1 for i in local_idx),
            figure=figure_display_name or "",
        )

        wf_path = (self._s.workflows_dir / f"{self._s.comfy_workflow_name}.json").resolve()
        gen_bundle = load_workflow_bundle(wf_path)
        up_path = (self._s.workflows_dir / f"{self._s.comfy_upscale_workflow_name}.json").resolve()
        up_bundle = load_upscale_bundle(up_path)

        comfy = ComfyClient(self._s)
        flux = build_flux_api_client(self._s)
        grok = build_grok_image_client(self._s)

        flux_raw_dir = out_dir / "_flux_raw"
        grok_raw_dir = out_dir / "_grok_raw"
        flux_raw_dir.mkdir(exist_ok=True)
        grok_raw_dir.mkdir(exist_ok=True)

        final: dict[int, Path] = {}
        min_w, min_h = up_bundle.min_width, up_bundle.min_height

        for i in sorted(flux_idx):
            out_path = out_dir / f"clause_{i:03d}.png"
            if resume and self._final_png_ready(comfy, out_path, min_w=min_w, min_h=min_h):
                final[i] = out_path
                log.info("triple_hybrid_flux_skip_done", clause=i + 1, total=total)
                if progress_cb:
                    progress_cb(i, prompts[i])
                continue
            raw_path = flux_raw_dir / f"clause_{i:03d}.png"
            if not (resume and raw_path.is_file() and raw_path.stat().st_size > 32):
                log.info(
                    "triple_hybrid_flux_generate",
                    clause=i + 1,
                    total=total,
                    flux_provider=self._s.flux_api_provider,
                    model=self._s.flux_api_model,
                    resolution=f"{self._s.flux_api_width}x{self._s.flux_api_height}",
                    prompt_preview=prompts[i][:80],
                )
                data = flux.generate(prompts[i])
                raw_path.write_bytes(data)
                log.info(
                    "triple_hybrid_flux_saved",
                    clause=i + 1,
                    size_kb=int(raw_path.stat().st_size / 1024),
                )
            log.info(
                "triple_hybrid_flux_upscale",
                clause=i + 1,
                total=total,
                workflow=self._s.comfy_upscale_workflow_name,
            )
            comfy.upscale_image(up_bundle, raw_path, out_path)
            final[i] = out_path
            if progress_cb:
                progress_cb(i, prompts[i])

        for i in sorted(grok_idx):
            out_path = out_dir / f"clause_{i:03d}.png"
            if resume and self._final_png_ready(comfy, out_path, min_w=min_w, min_h=min_h):
                final[i] = out_path
                log.info("triple_hybrid_grok_skip_done", clause=i + 1, total=total)
                if progress_cb:
                    progress_cb(i, prompts[i])
                continue
            raw_path = grok_raw_dir / f"clause_{i:03d}.png"
            grok_prompt = compose_grok_prompt(figure_display_name, prompts[i])
            if not (resume and raw_path.is_file() and raw_path.stat().st_size > 32):
                log.info(
                    "triple_hybrid_grok_generate",
                    clause=i + 1,
                    total=total,
                    model=self._s.grok_image_model,
                    prompt_preview=grok_prompt[:120],
                )
                raw_path.write_bytes(grok.generate(grok_prompt))
                log.info(
                    "triple_hybrid_grok_saved",
                    clause=i + 1,
                    size_kb=int(raw_path.stat().st_size / 1024),
                )
            log.info(
                "triple_hybrid_grok_upscale",
                clause=i + 1,
                total=total,
                workflow=self._s.comfy_upscale_workflow_name,
            )
            comfy.upscale_image(up_bundle, raw_path, out_path)
            final[i] = out_path
            if progress_cb:
                progress_cb(i, prompts[i])

        for i in sorted(local_idx):
            out_path = out_dir / f"clause_{i:03d}.png"
            if resume and self._final_png_ready(comfy, out_path, min_w=min_w, min_h=min_h):
                final[i] = out_path
                log.info("triple_hybrid_local_skip_done", clause=i + 1, total=total)
                if progress_cb:
                    progress_cb(i, prompts[i])
                continue
            log.info(
                "triple_hybrid_local_generate",
                clause=i + 1,
                total=total,
                workflow=self._s.comfy_workflow_name,
                prompt_preview=prompts[i][:80],
            )
            comfy.generate_one(gen_bundle, prompts[i], out_path)
            final[i] = out_path
            log.info(
                "triple_hybrid_local_done",
                clause=i + 1,
                size_kb=int(out_path.stat().st_size / 1024),
            )
            if progress_cb:
                progress_cb(i, prompts[i])

        missing = set(range(total)) - set(final.keys())
        if missing:
            raise RuntimeError(
                "Triple hybrid image generation incomplete. "
                f"Missing clause indices (0-based): {sorted(missing)}"
            )

        log.info("triple_hybrid_generate_done", total=total)
        return [final[i] for i in range(total)]
