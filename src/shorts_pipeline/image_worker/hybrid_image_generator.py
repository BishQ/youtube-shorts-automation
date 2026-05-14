"""Hybrid image generator: Flux Pro API for key frames, local ComfyUI for the rest.

Routing rule (0-based indices):
  • The LAST index always goes to the API.
  • The SECOND-TO-LAST index always stays local.
  • Every other even index goes to the API; every other odd index stays local.

Resulting splits by total clause count:
  • 12 clauses → API: {0,2,4,6,8,11},          Local: {1,3,5,7,9,10}            (6 + 6)
  • 13 clauses → API: {0,2,4,6,8,10,12},       Local: {1,3,5,7,9,11}            (7 + 6)
  • 14 clauses → API: {0,2,4,6,8,10,13},       Local: {1,3,5,7,9,11,12}         (7 + 7)

Pipeline for each API image:
  1. Submit to Flux Pro API at lower resolution (saves cost).
  2. Save raw bytes to <out_dir>/_api_raw/clause_NNN.png.
  3. After all local ComfyUI images are done, upload each raw API image to
     ComfyUI and run the upscale workflow → final clause_NNN.png.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from shorts_pipeline.config.settings import Settings
from shorts_pipeline.image_worker.comfy import (
    ComfyClient,
    load_upscale_bundle,
    load_workflow_bundle,
)
from shorts_pipeline.image_worker.flux_api_client import build_flux_api_client
from shorts_pipeline.image_worker.grok_bookend_generator import GrokBookendImageGenerator
from shorts_pipeline.logging_setup import get_logger

log = get_logger(__name__)


def api_indices(total: int) -> set[int]:
    """Return 0-based indices of clauses routed to the Flux Pro API.

    Rule: even indices (0,2,4,…) go to the API; EXCEPT the second-to-last
    index is always local. The last index is always API regardless of parity.

    Examples:
      12 clauses → API: {0,2,4,6,8,11},      Local: {1,3,5,7,9,10}      (6 + 6)
      13 clauses → API: {0,2,4,6,8,10,12},   Local: {1,3,5,7,9,11}      (7 + 6)
      14 clauses → API: {0,2,4,6,8,10,13},   Local: {1,3,5,7,9,11,12}   (7 + 7)
    """
    result: set[int] = set()
    for i in range(total):
        if i == total - 1:
            result.add(i)
        elif total >= 2 and i == total - 2:
            pass  # second-to-last is always local
        elif i % 2 == 0:
            result.add(i)
    return result


class HybridImageGenerator:
    """Route image generation between Flux Pro API and local ComfyUI Schnell."""

    def __init__(self, settings: Settings) -> None:
        self._s = settings

    def generate_all(
        self,
        prompts: list[str],
        out_dir: Path,
        *,
        progress_cb: Callable[[int, str], None] | None = None,
    ) -> list[Path]:
        """Generate all clause images and return their paths in clause order.

        Steps:
          1. Generate API images (fast parallel-ish external calls).
          2. Generate local ComfyUI images (sequential, GPU-bound).
          3. Upscale every raw API image through ComfyUI 4x UltraSharp.
          4. Return all paths sorted by clause index.
        """
        if not prompts:
            return []

        out_dir.mkdir(parents=True, exist_ok=True)
        total = len(prompts)
        api_idx = api_indices(total)
        local_idx = set(range(total)) - api_idx

        log.info(
            "hybrid_generate_start",
            total=total,
            api_count=len(api_idx),
            local_count=len(local_idx),
            api_clauses=sorted(i + 1 for i in api_idx),
            local_clauses=sorted(i + 1 for i in local_idx),
        )

        # Load ComfyUI workflow bundles
        wf_path = (self._s.workflows_dir / f"{self._s.comfy_workflow_name}.json").resolve()
        gen_bundle = load_workflow_bundle(wf_path)

        up_path = (self._s.workflows_dir / f"{self._s.comfy_upscale_workflow_name}.json").resolve()
        up_bundle = load_upscale_bundle(up_path)

        comfy = ComfyClient(self._s)
        flux = build_flux_api_client(self._s)

        api_raw_dir = out_dir / "_api_raw"
        api_raw_dir.mkdir(exist_ok=True)

        final: dict[int, Path] = {}

        # ── Step 1: Generate API images (lower-res, fast) ─────────────────────
        for i in sorted(api_idx):
            log.info(
                "hybrid_api_generate",
                clause=i + 1,
                total=total,
                flux_provider=self._s.flux_api_provider,
                model=self._s.flux_api_model,
                resolution=f"{self._s.flux_api_width}x{self._s.flux_api_height}",
                prompt_preview=prompts[i][:80],
            )
            raw_path = api_raw_dir / f"clause_{i:03d}.png"
            data = flux.generate(prompts[i])
            raw_path.write_bytes(data)
            log.info(
                "hybrid_api_saved",
                clause=i + 1,
                size_kb=int(raw_path.stat().st_size / 1024),
            )

        # ── Step 2: Generate local ComfyUI images ─────────────────────────────
        for i in sorted(local_idx):
            log.info(
                "hybrid_local_generate",
                clause=i + 1,
                total=total,
                workflow=self._s.comfy_workflow_name,
                prompt_preview=prompts[i][:80],
            )
            out_path = out_dir / f"clause_{i:03d}.png"
            comfy.generate_one(gen_bundle, prompts[i], out_path)
            final[i] = out_path
            log.info("hybrid_local_done", clause=i + 1, size_kb=int(out_path.stat().st_size / 1024))
            if progress_cb:
                progress_cb(i, prompts[i])

        # ── Step 3: Upscale API raw images through ComfyUI ────────────────────
        for i in sorted(api_idx):
            raw_path = api_raw_dir / f"clause_{i:03d}.png"
            out_path = out_dir / f"clause_{i:03d}.png"
            log.info(
                "hybrid_upscale",
                clause=i + 1,
                total=total,
                workflow=self._s.comfy_upscale_workflow_name,
                raw_size_kb=int(raw_path.stat().st_size / 1024),
            )
            comfy.upscale_image(up_bundle, raw_path, out_path)
            final[i] = out_path
            if progress_cb:
                progress_cb(i, prompts[i])

        # ── Validate completeness ─────────────────────────────────────────────
        missing = set(range(total)) - set(final.keys())
        if missing:
            raise RuntimeError(
                f"Hybrid image generation incomplete. "
                f"Missing clause indices (0-based): {sorted(missing)}"
            )

        log.info("hybrid_generate_done", total=total)
        return [final[i] for i in range(total)]


def build_image_generator(settings: Settings) -> Any:
    """Return the correct image generator for settings.image_backend."""
    backend = settings.image_backend.lower().strip()

    if backend == "hybrid":
        if not settings.flux_api_key:
            raise ValueError(
                "image_backend='hybrid' requires SHORTS_FLUX_API_KEY. "
                "Use your BFL key (api.bfl.ml) or Together key (together.ai) "
                "depending on SHORTS_FLUX_API_PROVIDER."
            )
        return HybridImageGenerator(settings)

    if backend == "hybrid_grok_bookends":
        if not settings.grok_api_key:
            raise ValueError(
                "image_backend='hybrid_grok_bookends' requires SHORTS_GROK_API_KEY "
                "(xAI API key for Grok Imagine)."
            )
        return GrokBookendImageGenerator(settings)

    if backend == "hybrid_grok_flux":
        from shorts_pipeline.image_worker.triple_hybrid_image_generator import (
            TripleHybridImageGenerator,
        )

        if not settings.flux_api_key:
            raise ValueError(
                "image_backend='hybrid_grok_flux' requires SHORTS_FLUX_API_KEY "
                "(Together or BFL, per SHORTS_FLUX_API_PROVIDER)."
            )
        if not settings.grok_api_key:
            raise ValueError(
                "image_backend='hybrid_grok_flux' requires SHORTS_GROK_API_KEY "
                "(xAI Grok Imagine for first/last and optional extras)."
            )
        return TripleHybridImageGenerator(settings)

    if backend == "comfy":
        return None  # caller uses ComfyClient directly

    raise ValueError(
        "Unknown image_backend {0!r}. Valid values: 'comfy', 'hybrid', "
        "'hybrid_grok_bookends', 'hybrid_grok_flux'.".format(backend)
    )
