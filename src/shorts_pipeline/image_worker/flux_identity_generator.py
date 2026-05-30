"""Flux 2 Dev identity images: PuLID + InstantID keypoints + ControlNet inpaint."""

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
from shorts_pipeline.image_worker.face_mask import build_face_inpaint_mask
from shorts_pipeline.image_worker.reference_images import (
    PersonReferences,
    resolve_person_references,
)
from shorts_pipeline.logging_setup import get_logger
from shorts_pipeline.runpod_adapter import make_image_client

log = get_logger(__name__)

PORTRAIT_W = 832
PORTRAIT_H = 1472


@dataclass
class FluxIdentityBundle(WorkflowBundle):
    """Flux2 + PuLID + InstantID kps + ControlNet inpaint workflow bundle."""

    reference_image_key_paths: list[list[str | int]]
    face_mask_key_path: list[str | int]
    pulid_model_key_path: list[str | int]
    pulid_strength_key_paths: list[list[str | int]]
    controlnet_name_key_path: list[str | int]
    controlnet_strength_key_path: list[str | int]
    inpaint_denoise_key_path: list[str | int]
    seed_key_path: list[str | int] | None = None


def load_flux_identity_bundle(path: Path) -> FluxIdentityBundle:
    raw = __import__("json").loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ComfyError("flux identity workflow must be a JSON object")
    base = load_workflow_bundle(path)
    meta = raw.get("meta") or {}
    refs = meta.get("reference_image_key_paths")
    fmk = meta.get("face_mask_key_path")
    pmk = meta.get("pulid_model_key_path")
    psk = meta.get("pulid_strength_key_paths")
    cnk = meta.get("controlnet_name_key_path")
    csk = meta.get("controlnet_strength_key_path")
    idk = meta.get("inpaint_denoise_key_path")
    for name, val in (
        ("reference_image_key_paths", refs),
        ("face_mask_key_path", fmk),
        ("pulid_model_key_path", pmk),
        ("pulid_strength_key_paths", psk),
        ("controlnet_name_key_path", cnk),
        ("controlnet_strength_key_path", csk),
        ("inpaint_denoise_key_path", idk),
    ):
        if not isinstance(val, list) or not val:
            raise ComfyError(f"flux identity meta.{name} must be a non-empty list")
    sk = meta.get("seed_key_path")
    seed_path: list[str | int] | None = sk if isinstance(sk, list) and sk else None
    return FluxIdentityBundle(
        prompt=base.prompt,
        prompt_key_path=base.prompt_key_path,
        min_width=base.min_width,
        min_height=base.min_height,
        reference_image_key_paths=refs,
        face_mask_key_path=fmk,
        pulid_model_key_path=pmk,
        pulid_strength_key_paths=psk,
        controlnet_name_key_path=cnk,
        controlnet_strength_key_path=csk,
        inpaint_denoise_key_path=idk,
        seed_key_path=seed_path,
    )


class _FluxIdentityCapable:
    def _pulid_strengths(self, settings: Settings) -> list[float]:
        return [
            float(settings.flux_pulid_strength_primary),
            float(settings.flux_pulid_strength_ref2),
            float(settings.flux_pulid_strength_ref3),
            float(settings.flux_pulid_strength_ref4),
        ]

    def _patch_identity_workflow(
        self,
        bundle: FluxIdentityBundle,
        *,
        prompt_text: str,
        ref_uploaded_names: list[str],
        face_mask_name: str,
        settings: Settings,
        seed: int,
    ) -> dict[str, Any]:
        wf = copy.deepcopy(bundle.prompt)
        _nested_set(wf, bundle.prompt_key_path, prompt_text)
        _patch_workflow(wf, prompt_text, seed)
        if bundle.seed_key_path:
            _nested_set(wf, bundle.seed_key_path, seed)
        _nested_set(wf, ["63", "inputs", "seed"], seed)

        pulid_name = (settings.flux_pulid_model_name or "pulid_flux2_klein_v2.safetensors").strip()
        _nested_set(wf, bundle.pulid_model_key_path, pulid_name)

        cn_name = (settings.flux_controlnet_name or "").strip()
        if cn_name:
            _nested_set(wf, bundle.controlnet_name_key_path, cn_name)

        for path, strength in zip(bundle.pulid_strength_key_paths, self._pulid_strengths(settings)):
            _nested_set(wf, path, strength)

        _nested_set(wf, bundle.controlnet_strength_key_path, float(settings.flux_inpaint_controlnet_strength))
        _nested_set(wf, bundle.inpaint_denoise_key_path, float(settings.flux_inpaint_denoise))

        for i, key_path in enumerate(bundle.reference_image_key_paths):
            if i >= len(ref_uploaded_names):
                break
            _nested_set(wf, key_path, ref_uploaded_names[i])
        _nested_set(wf, bundle.face_mask_key_path, face_mask_name)
        return wf


def generate_flux_identity_one(
    comfy: ComfyClient,
    bundle: FluxIdentityBundle,
    prompt_text: str,
    out_path: Path,
    *,
    refs: PersonReferences,
    settings: Settings,
    seed: int | None = None,
) -> None:
    cap = _FluxIdentityCapable()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    actual_seed = seed if seed is not None else random.randint(1, 2**32 - 1)

    uploaded: list[str] = []
    for p in refs.image_paths:
        uploaded.append(comfy.upload_image(p))

    mask_path = build_face_inpaint_mask(
        refs.image_paths[0],
        width=PORTRAIT_W,
        height=PORTRAIT_H,
    )
    mask_name = comfy.upload_image(mask_path)

    wf = cap._patch_identity_workflow(
        bundle,
        prompt_text=prompt_text,
        ref_uploaded_names=uploaded,
        face_mask_name=mask_name,
        settings=settings,
        seed=actual_seed,
    )
    log.info(
        "flux_identity_generate_start",
        refs=len(uploaded),
        figure=refs.figure_name,
        qid=refs.wikidata_id,
        pulid=settings.flux_pulid_model_name,
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


class FluxIdentityComfyMixin(_FluxIdentityCapable):
    _comfy: ComfyClient

    def generate_flux_identity_one(
        self,
        bundle: FluxIdentityBundle,
        prompt_text: str,
        out_path: Path,
        *,
        refs: PersonReferences,
        settings: Settings,
        seed: int | None = None,
    ) -> None:
        generate_flux_identity_one(
            self._comfy,
            bundle,
            prompt_text,
            out_path,
            refs=refs,
            settings=settings,
            seed=seed,
        )


class FluxIdentityImageGenerator:
    """4 ref photos + PuLID + InstantID kps + Flux ControlNet inpaint (no LoRA train)."""

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
        refs = resolve_person_references(figure_name, root=self._s.reference_images_root)
        if refs is None:
            raise ComfyError(
                f"No reference images found for figure {figure_name!r} under "
                f"{self._s.reference_images_root}"
            )

        wf_name = self._s.flux_identity_workflow_name or self._s.comfy_workflow_name
        bundle_path = (self._s.workflows_dir / f"{wf_name}.json").resolve()
        bundle = load_flux_identity_bundle(bundle_path)

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
                    "backend": "flux_identity",
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
        bundle: FluxIdentityBundle,
        prompt_text: str,
        out_path: Path,
        *,
        refs: PersonReferences,
    ) -> None:
        if hasattr(self._client, "generate_flux_identity_one"):
            self._client.generate_flux_identity_one(
                bundle,
                prompt_text,
                out_path,
                refs=refs,
                settings=self._s,
            )
            return
        if isinstance(self._client, ComfyClient):
            generate_flux_identity_one(
                self._client,
                bundle,
                prompt_text,
                out_path,
                refs=refs,
                settings=self._s,
            )
            return
        raise ComfyError(f"image client {type(self._client).__name__} lacks flux identity support")
