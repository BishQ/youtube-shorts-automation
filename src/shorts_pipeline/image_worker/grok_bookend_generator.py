"""Grok Imagine for bookend (+ optional) frames; local ComfyUI for all others.

Routing (0-based clause indices):
  • Always Grok: first (0) and last (n-1).
  • Optional extra Grok clauses: SHORTS_GROK_EXTRA_CLAUSE_INDICES (comma-separated),
    e.g. "6,7" for emotional mid-shots that should keep the same face.

Each Grok output is saved under images/_grok_raw/ then upscaled with the same
ComfyUI workflow as hybrid Flux API frames.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from shorts_pipeline.config.settings import Settings
from shorts_pipeline.image_worker.comfy import (
    ComfyClient,
    load_upscale_bundle,
    load_workflow_bundle,
)
from shorts_pipeline.image_worker.grok_image_client import build_grok_image_client
from shorts_pipeline.logging_setup import get_logger

log = get_logger(__name__)

_MAX_PROMPT_CHARS = 4000


def grok_clause_indices(total: int, extra_indices_csv: str | None) -> set[int]:
    """Indices that use Grok Imagine: always 0 and total-1, plus validated extras."""
    if total <= 0:
        return set()
    out: set[int] = {0, total - 1}
    raw = (extra_indices_csv or "").strip()
    if not raw:
        return out
    for part in raw.split(","):
        p = part.strip()
        if not p:
            continue
        if not re.fullmatch(r"-?\d+", p):
            raise ValueError(
                f"Invalid SHORTS_GROK_EXTRA_CLAUSE_INDICES segment {part!r} "
                "(use comma-separated integers, 0-based clause indices)."
            )
        i = int(p)
        if i < 0 or i >= total:
            raise ValueError(
                f"Grok extra clause index {i} out of range for {total} clauses (valid 0..{total - 1})."
            )
        out.add(i)
    return out


def compose_grok_prompt(figure_display_name: str | None, clause_prompt: str) -> str:
    """Prepend the figure name so Grok locks onto the correct likeness every frame."""
    scene = (clause_prompt or "").strip()
    name = (figure_display_name or "").strip()
    if name:
        prefix = (
            f"Photorealistic vertical 9:16 cinematic still. Subject: {name} — "
            f"same person, same face, consistent likeness across every frame. "
            f"Ultra-detailed face, era-accurate clothing, dramatic professional lighting. "
            f"Scene: "
        )
        merged = prefix + scene
    else:
        merged = scene
    if len(merged) > _MAX_PROMPT_CHARS:
        return merged[:_MAX_PROMPT_CHARS]
    return merged


class GrokBookendImageGenerator:
    """Grok Imagine for configured clause indices; ComfyUI for the rest."""

    def __init__(self, settings: Settings) -> None:
        self._s = settings

    def generate_all(
        self,
        prompts: list[str],
        out_dir: Path,
        *,
        figure_display_name: str | None = None,
        progress_cb: Callable[[int, str], None] | None = None,
    ) -> list[Path]:
        if not prompts:
            return []

        out_dir.mkdir(parents=True, exist_ok=True)
        total = len(prompts)
        grok_idx = grok_clause_indices(total, self._s.grok_extra_clause_indices)
        local_idx = set(range(total)) - grok_idx

        log.info(
            "grok_bookend_generate_start",
            total=total,
            grok_clauses=sorted(i + 1 for i in grok_idx),
            local_clauses=sorted(i + 1 for i in local_idx),
            figure=figure_display_name or "",
        )

        wf_path = (self._s.workflows_dir / f"{self._s.comfy_workflow_name}.json").resolve()
        gen_bundle = load_workflow_bundle(wf_path)
        up_path = (self._s.workflows_dir / f"{self._s.comfy_upscale_workflow_name}.json").resolve()
        up_bundle = load_upscale_bundle(up_path)

        comfy = ComfyClient(self._s)
        grok = build_grok_image_client(self._s)

        grok_raw_dir = out_dir / "_grok_raw"
        grok_raw_dir.mkdir(exist_ok=True)

        final: dict[int, Path] = {}

        for i in sorted(local_idx):
            log.info(
                "grok_bookend_local_generate",
                clause=i + 1,
                total=total,
                workflow=self._s.comfy_workflow_name,
                prompt_preview=prompts[i][:80],
            )
            out_path = out_dir / f"clause_{i:03d}.png"
            comfy.generate_one(gen_bundle, prompts[i], out_path)
            final[i] = out_path
            log.info("grok_bookend_local_done", clause=i + 1, size_kb=int(out_path.stat().st_size / 1024))
            if progress_cb:
                progress_cb(i, prompts[i])

        for i in sorted(grok_idx):
            grok_prompt = compose_grok_prompt(figure_display_name, prompts[i])
            log.info(
                "grok_bookend_api_generate",
                clause=i + 1,
                total=total,
                model=self._s.grok_image_model,
                prompt_preview=grok_prompt[:120],
            )
            raw_path = grok_raw_dir / f"clause_{i:03d}.png"
            data = grok.generate(grok_prompt)
            raw_path.write_bytes(data)
            log.info(
                "grok_bookend_api_saved",
                clause=i + 1,
                size_kb=int(raw_path.stat().st_size / 1024),
            )

        for i in sorted(grok_idx):
            raw_path = grok_raw_dir / f"clause_{i:03d}.png"
            out_path = out_dir / f"clause_{i:03d}.png"
            log.info(
                "grok_bookend_upscale",
                clause=i + 1,
                total=total,
                workflow=self._s.comfy_upscale_workflow_name,
                raw_size_kb=int(raw_path.stat().st_size / 1024),
            )
            comfy.upscale_image(up_bundle, raw_path, out_path)
            final[i] = out_path
            if progress_cb:
                progress_cb(i, prompts[i])

        missing = set(range(total)) - set(final.keys())
        if missing:
            raise RuntimeError(
                "Grok bookend image generation incomplete. "
                f"Missing clause indices (0-based): {sorted(missing)}"
            )

        log.info("grok_bookend_generate_done", total=total)
        return [final[i] for i in range(total)]
