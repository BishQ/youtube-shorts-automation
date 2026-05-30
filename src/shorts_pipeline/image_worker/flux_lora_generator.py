"""Flux 2 Dev + LoRA image generation with up to 4 person reference photos."""

from __future__ import annotations

import copy
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from shorts_pipeline.config.settings import Settings
from shorts_pipeline.image_worker.comfy import (
    ComfyClient,
    ComfyError,
    WorkflowBundle,
    _nested_set,
    _patch_workflow,
    load_workflow_bundle,
)
from shorts_pipeline.image_worker.reference_images import (
    PersonReferences,
    resolve_person_references,
)
from shorts_pipeline.logging_setup import get_logger
from shorts_pipeline.runpod_adapter import make_image_client

log = get_logger(__name__)


@dataclass
class FluxLoraBundle(WorkflowBundle):
    """Flux2 + LoRA + multi-reference workflow bundle."""

    lora_key_path: list[str | int]
    lora_strength_key_path: list[str | int]
    reference_image_key_paths: list[list[str | int]]


def load_flux_lora_bundle(path: Path) -> FluxLoraBundle:
    raw = __import__("json").loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ComfyError("flux lora workflow must be a JSON object")
    base = load_workflow_bundle(path)
    meta = raw.get("meta") or {}
    lk = meta.get("lora_key_path")
    lsk = meta.get("lora_strength_key_path")
    refs = meta.get("reference_image_key_paths")
    if not isinstance(lk, list) or not lk:
        raise ComfyError("meta.lora_key_path required for flux lora workflow")
    if not isinstance(lsk, list) or not lsk:
        raise ComfyError("meta.lora_strength_key_path required for flux lora workflow")
    if not isinstance(refs, list) or len(refs) < 1:
        raise ComfyError("meta.reference_image_key_paths required (1–4 LoadImage paths)")
    return FluxLoraBundle(
        prompt=base.prompt,
        prompt_key_path=base.prompt_key_path,
        min_width=base.min_width,
        min_height=base.min_height,
        lora_key_path=lk,
        lora_strength_key_path=lsk,
        reference_image_key_paths=refs,
    )


class _FluxLoraCapable:
    """Shared patch + queue logic for local Comfy and RunPod adapters."""

    def _resolve_lora_name(self, settings: Settings, refs: PersonReferences) -> str:
        if settings.flux_person_lora_dir:
            lora_dir = Path(settings.flux_person_lora_dir)
            for stem in (refs.lora_stem(), refs.figure_name.replace(" ", "_")):
                for name in (f"{stem}.safetensors", f"{stem.lower()}.safetensors"):
                    if (lora_dir / name).is_file():
                        return name
        name = (settings.flux_lora_name or "").strip()
        if not name:
            raise ComfyError(
                "flux_lora_name unset and no per-person LoRA found — "
                "set SHORTS_FLUX_LORA_NAME or SHORTS_FLUX_PERSON_LORA_DIR"
            )
        return name

    def _patch_flux_workflow(
        self,
        bundle: FluxLoraBundle,
        *,
        prompt_text: str,
        lora_name: str,
        lora_strength: float,
        ref_uploaded_names: list[str],
        seed: int,
    ) -> dict[str, Any]:
        wf = copy.deepcopy(bundle.prompt)
        _nested_set(wf, bundle.prompt_key_path, prompt_text)
        _nested_set(wf, bundle.lora_key_path, lora_name)
        _nested_set(wf, bundle.lora_strength_key_path, lora_strength)
        for i, key_path in enumerate(bundle.reference_image_key_paths):
            if i >= len(ref_uploaded_names):
                break
            _nested_set(wf, key_path, ref_uploaded_names[i])
        _patch_workflow(wf, prompt_text, seed)
        return wf


def generate_flux_lora_one(
    comfy: ComfyClient,
    bundle: FluxLoraBundle,
    prompt_text: str,
    out_path: Path,
    *,
    refs: PersonReferences,
    settings: Settings,
    seed: int | None = None,
) -> None:
    """Generate one clause image via Flux2 + LoRA + 4 reference uploads."""
    cap = _FluxLoraCapable()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lora_name = cap._resolve_lora_name(settings, refs)
    uploaded: list[str] = []
    for p in refs.image_paths:
        uploaded.append(comfy.upload_image(p))
    actual_seed = seed if seed is not None else random.randint(1, 2**32 - 1)
    wf = cap._patch_flux_workflow(
        bundle,
        prompt_text=prompt_text,
        lora_name=lora_name,
        lora_strength=float(settings.flux_lora_strength),
        ref_uploaded_names=uploaded,
        seed=actual_seed,
    )
    log.info(
        "flux_lora_generate_start",
        lora=lora_name,
        refs=len(uploaded),
        figure=refs.figure_name,
        qid=refs.wikidata_id,
        prompt_preview=prompt_text[:80],
    )
    pid = comfy.queue_prompt(wf)
    hist = comfy.wait_for_completion(pid)
    outputs = hist.get("outputs") or {}
    from shorts_pipeline.image_worker.comfy import _pick_last_png

    png_name, subfolder = _pick_last_png(outputs)
    data = comfy.fetch_output_png(png_name, subfolder=subfolder)
    out_path.write_bytes(data)
    comfy.verify_png(out_path, min_w=bundle.min_width, min_h=bundle.min_height)


class FluxLoraComfyMixin(_FluxLoraCapable):
    _comfy: ComfyClient

    def generate_flux_lora_one(
        self,
        bundle: FluxLoraBundle,
        prompt_text: str,
        out_path: Path,
        *,
        refs: PersonReferences,
        settings: Settings,
        seed: int | None = None,
    ) -> None:
        generate_flux_lora_one(
            self._comfy,
            bundle,
            prompt_text,
            out_path,
            refs=refs,
            settings=settings,
            seed=seed,
        )


class FluxLoraImageGenerator:
    """Generate clause images via Flux 2 Dev + person LoRA + 4 reference photos."""

    def __init__(self, settings: Settings) -> None:
        self._s = settings
        self._client = make_image_client(settings)

    def generate_all(
        self,
        prompts: list[str],
        out_dir: Path,
        *,
        figure_name: str,
        progress_cb: Callable[[int, str], None] | None = None,
        resume: bool = True,
    ) -> list[Path]:
        refs = resolve_person_references(
            figure_name,
            root=self._s.reference_images_root,
        )
        if refs is None:
            raise ComfyError(
                f"No reference images found for figure {figure_name!r} under "
                f"{self._s.reference_images_root}"
            )

        wf_name = self._s.flux_lora_workflow_name or self._s.comfy_workflow_name
        bundle_path = (self._s.workflows_dir / f"{wf_name}.json").resolve()
        bundle = load_flux_lora_bundle(bundle_path)

        out_dir.mkdir(parents=True, exist_ok=True)
        manifest = out_dir / "reference_manifest.json"
        manifest.write_text(
            __import__("json").dumps(
                {
                    "figure_name": refs.figure_name,
                    "label": refs.label,
                    "index": refs.index,
                    "wikidata_id": refs.wikidata_id,
                    "batch": refs.batch_name,
                    "images": [str(p) for p in refs.image_paths],
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        paths: list[Path] = []
        for i, ptxt in enumerate(prompts):
            out_path = out_dir / f"clause_{i:03d}.png"
            if resume and out_path.is_file() and out_path.stat().st_size > 1000:
                paths.append(out_path)
                continue
            self._generate_one(bundle, ptxt, out_path, refs=refs)
            paths.append(out_path)
            if progress_cb:
                progress_cb(i, ptxt)
        return paths

    def _generate_one(
        self,
        bundle: FluxLoraBundle,
        prompt_text: str,
        out_path: Path,
        *,
        refs: PersonReferences,
    ) -> None:
        if hasattr(self._client, "generate_flux_lora_one"):
            self._client.generate_flux_lora_one(
                bundle,
                prompt_text,
                out_path,
                refs=refs,
                settings=self._s,
            )
            return
        if isinstance(self._client, ComfyClient):
            generate_flux_lora_one(
                self._client,
                bundle,
                prompt_text,
                out_path,
                refs=refs,
                settings=self._s,
            )
            return
        raise ComfyError(f"image client {type(self._client).__name__} lacks flux lora support")
