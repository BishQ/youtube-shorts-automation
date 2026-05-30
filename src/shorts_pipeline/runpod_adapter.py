"""RunPod Serverless adapter for ComfyUI workflows.

Drop-in replacement for `image_worker.comfy.ComfyClient` and
`video_worker.wan_i2v.WanI2VClient` when running against RunPod's serverless
ComfyUI worker (https://github.com/runpod-workers/worker-comfyui).

## Why a separate adapter?

The local `ComfyClient` talks to a long-running ComfyUI server with stateful
endpoints (`/upload/image`, `/prompt`, `/history`, `/view`). RunPod Serverless
is request/response with a different shape:

    POST https://api.runpod.ai/v2/{endpoint_id}/run
        Body: {"input": {"workflow": {...}, "images": [{"name": "x.png", "image": "<base64>"}]}}
        → {"id": "...", "status": "IN_QUEUE"}
    GET  https://api.runpod.ai/v2/{endpoint_id}/status/{id}
        → {"status": "COMPLETED", "output": {"message": "<base64>", "filename": "..."}}

We collapse "upload image + queue workflow + fetch result" into one POST and
one polling loop. The same workflow JSON works in both modes — only the
transport changes.

## Public surface

Both adapter classes match the local clients' generate-call signatures exactly,
so the orchestrator can pick which backend to instantiate based on
`settings.comfy_mode` without further changes downstream.

    RunPodComfyClient(settings).generate_one(bundle, prompt_text, out_path)
    RunPodWanI2VClient(settings).generate_clip(
        bundle, image_path=..., motion_prompt=..., duration_s=..., out_path=...
    )

## RunPod worker output schema

The runpod-workers/worker-comfyui handler reports output as:
    {"output": {"message": "<base64-encoded-bytes>", "status": "success"}}
for the most-recently-saved file. Some forks return:
    {"output": [{"filename": "x.png", "type": "base64", "data": "..."}]}
We accept both shapes — see `_decode_runpod_output`.
"""

from __future__ import annotations

import base64
import copy
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from PIL import Image

from shorts_pipeline.config.settings import Settings
from shorts_pipeline.image_worker.comfy import (
    ComfyError,
    WorkflowBundle,
    _nested_set,
    _patch_workflow,
)
from shorts_pipeline.logging_setup import get_logger
from shorts_pipeline.video_worker.wan_i2v import (
    WanI2VBundle,
    WanI2VError,
    _patch_seeds,
    duration_to_frames,
)

log = get_logger(__name__)


class RunPodError(ComfyError):
    """RunPod-specific failure. Inherits ComfyError so callers can catch either."""


# ── HTTP layer ────────────────────────────────────────────────────────────────


@dataclass
class _RunPodConfig:
    """Pre-validated RunPod settings extracted from the Settings object.

    Validation lives here (not inside Settings) so that local-mode users can
    leave runpod_* unset without tripping pydantic.
    """
    endpoint_id: str
    api_key: str
    base_url: str
    poll_interval_s: float
    max_polls: int
    request_timeout_s: float

    @classmethod
    def from_settings(cls, s: Settings) -> _RunPodConfig:
        if not s.runpod_endpoint_id:
            raise RunPodError(
                "comfy_mode='runpod' but runpod_endpoint_id is unset. "
                "Set SHORTS_RUNPOD_ENDPOINT_ID env var."
            )
        if not s.runpod_api_key:
            raise RunPodError(
                "comfy_mode='runpod' but runpod_api_key is unset. "
                "Set SHORTS_RUNPOD_API_KEY env var."
            )
        return cls(
            endpoint_id=s.runpod_endpoint_id,
            api_key=s.runpod_api_key,
            base_url=s.runpod_base_url.rstrip("/"),
            poll_interval_s=s.runpod_poll_interval_s,
            max_polls=s.runpod_max_polls,
            request_timeout_s=s.runpod_request_timeout_s,
        )


def _submit_job(cfg: _RunPodConfig, payload: dict[str, Any]) -> str:
    """POST /v2/{endpoint_id}/run and return the job id.

    We always use async /run (not /runsync). Wan I2V can take 5-8 minutes per
    clip and /runsync caps at 10 min including queue time — too tight.
    """
    url = f"{cfg.base_url}/{cfg.endpoint_id}/run"
    headers = {
        "Authorization": f"Bearer {cfg.api_key}",
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=cfg.request_timeout_s) as client:
        try:
            r = client.post(url, headers=headers, json=payload)
        except httpx.RequestError as e:
            raise RunPodError(f"RunPod /run connection error: {e}") from e
    if r.status_code == 401:
        raise RunPodError("RunPod /run 401 — check runpod_api_key")
    if r.status_code == 404:
        raise RunPodError(f"RunPod /run 404 — endpoint {cfg.endpoint_id} not found")
    if r.status_code >= 400:
        raise RunPodError(
            f"RunPod /run HTTP {r.status_code}",
            status_code=r.status_code,
            detail=r.text[:2000],
        )
    data = r.json()
    job_id = data.get("id")
    if not isinstance(job_id, str):
        raise RunPodError("RunPod /run missing job id in response", detail=data)
    return job_id


def _poll_until_complete(cfg: _RunPodConfig, job_id: str) -> dict[str, Any]:
    """Poll /v2/{endpoint_id}/status/{id} until COMPLETED, FAILED, or timeout.

    Returns the parsed response dict (not just output) so callers can inspect
    `executionTime`, `delayTime`, etc. for cost logging.
    """
    url = f"{cfg.base_url}/{cfg.endpoint_id}/status/{job_id}"
    headers = {"Authorization": f"Bearer {cfg.api_key}"}
    last_status: str | None = None
    for attempt in range(cfg.max_polls):
        with httpx.Client(timeout=cfg.request_timeout_s) as client:
            try:
                r = client.get(url, headers=headers)
            except httpx.RequestError as e:
                # Transient network blip — log and keep polling rather than killing
                # the whole 8-minute job over a flaky GET.
                log.warning("runpod_status_transient_error", job_id=job_id, error=str(e))
                time.sleep(cfg.poll_interval_s)
                continue
        if r.status_code >= 500:
            log.warning("runpod_status_5xx", job_id=job_id, status=r.status_code)
            time.sleep(cfg.poll_interval_s)
            continue
        if r.status_code >= 400:
            raise RunPodError(
                f"RunPod /status HTTP {r.status_code} for job {job_id}",
                status_code=r.status_code,
                detail=r.text[:2000],
            )
        data = r.json()
        status = data.get("status")
        if status != last_status:
            log.info(
                "runpod_status",
                job_id=job_id,
                status=status,
                attempt=attempt,
                delay_ms=data.get("delayTime"),
            )
            last_status = status
        if status == "COMPLETED":
            return data
        if status in ("FAILED", "CANCELLED", "TIMED_OUT"):
            raise RunPodError(
                f"RunPod job {job_id} terminated with status {status}",
                detail=data,
            )
        # IN_QUEUE or IN_PROGRESS — keep polling
        time.sleep(cfg.poll_interval_s)
    raise RunPodError(
        f"RunPod job {job_id} did not complete within "
        f"{cfg.max_polls * cfg.poll_interval_s:.0f}s",
    )


def _decode_runpod_output(output: Any, *, expected_ext: str) -> bytes:
    """Decode the bytes payload from RunPod, regardless of worker variant.

    The community of ComfyUI worker images returns the file in one of these
    shapes; we try each in order:

      1. {"message": "<base64>"}                    — runpod-workers/worker-comfyui
      2. {"<filename>": "<base64>"}                 — some forks
      3. [{"filename": "...", "data": "<base64>"}]  — older Ashley fork
      4. {"images": [{"data": "..."}]}              — composite output
    """
    def _b64(s: str) -> bytes:
        try:
            return base64.b64decode(s, validate=False)
        except Exception as e:
            raise RunPodError(f"failed to base64-decode output: {e}") from e

    if isinstance(output, dict):
        # Shape 1: explicit "message" key
        msg = output.get("message")
        if isinstance(msg, str) and len(msg) > 100:
            return _b64(msg)
        # Shape 4: nested "images"
        imgs = output.get("images") or output.get("videos") or output.get("gifs")
        if isinstance(imgs, list) and imgs:
            first = imgs[0]
            if isinstance(first, dict):
                blob = first.get("data") or first.get("image") or first.get("video")
                if isinstance(blob, str):
                    return _b64(blob)
        # Shape 2: filename key with base64 value
        for k, v in output.items():
            if isinstance(k, str) and k.lower().endswith(expected_ext) and isinstance(v, str):
                return _b64(v)
        raise RunPodError(
            "RunPod output dict had no recognized base64 payload",
            detail={"keys": list(output.keys())},
        )
    if isinstance(output, list) and output:
        # Shape 3
        first = output[0]
        if isinstance(first, dict):
            blob = first.get("data") or first.get("image") or first.get("video")
            if isinstance(blob, str):
                return _b64(blob)
    raise RunPodError(
        f"unrecognized RunPod output structure (type={type(output).__name__})",
        detail={"output_preview": str(output)[:300]},
    )


# ── Image generation client (Qwen-Image, Flux, etc.) ──────────────────────────


class RunPodComfyClient:
    """RunPod-backed image generator. Mirrors ComfyClient.generate_one()."""

    def __init__(self, settings: Settings) -> None:
        self._s = settings
        self._cfg = _RunPodConfig.from_settings(settings)

    def generate_one(
        self,
        bundle: WorkflowBundle,
        prompt_text: str,
        out_path: Path,
        *,
        seed: int | None = None,
    ) -> None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        wf = copy.deepcopy(bundle.prompt)
        _nested_set(wf, bundle.prompt_key_path, prompt_text)
        actual_seed = seed if seed is not None else random.randint(1, 2**32 - 1)
        _patch_workflow(wf, prompt_text, actual_seed)

        payload = {"input": {"workflow": wf}}
        log.info(
            "runpod_image_submit",
            workflow_nodes=len(wf),
            prompt_preview=prompt_text[:80],
            seed=actual_seed,
        )
        job_id = _submit_job(self._cfg, payload)
        result = _poll_until_complete(self._cfg, job_id)

        output = result.get("output")
        if output is None:
            raise RunPodError("RunPod /status COMPLETED but output is null", detail=result)
        png_bytes = _decode_runpod_output(output, expected_ext=".png")
        out_path.write_bytes(png_bytes)

        # Verify locally — same gate as the local ComfyClient.
        try:
            with Image.open(out_path) as im:
                im.verify()
            with Image.open(out_path) as im2:
                w, h = im2.size
        except Exception as e:
            raise RunPodError(f"corrupt PNG from RunPod: {out_path}: {e}") from e
        if w < bundle.min_width or h < bundle.min_height:
            raise RunPodError(
                f"RunPod image too small: {w}x{h} < {bundle.min_width}x{bundle.min_height}"
            )

        log.info(
            "runpod_image_done",
            out=out_path.name,
            size_kb=int(out_path.stat().st_size / 1024),
            exec_ms=result.get("executionTime"),
            delay_ms=result.get("delayTime"),
        )

    def generate_flux_lora_one(
        self,
        bundle: Any,
        prompt_text: str,
        out_path: Path,
        *,
        refs: Any,
        settings: Settings,
        seed: int | None = None,
    ) -> None:
        from shorts_pipeline.image_worker.flux_lora_generator import (
            FluxLoraBundle,
            _FluxLoraCapable,
        )

        if not isinstance(bundle, FluxLoraBundle):
            raise RunPodError("generate_flux_lora_one requires FluxLoraBundle")
        cap = _FluxLoraCapable()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        lora_name = cap._resolve_lora_name(settings, refs)
        actual_seed = seed if seed is not None else random.randint(1, 2**32 - 1)
        images_payload: list[dict[str, str]] = []
        ref_names: list[str] = []
        for i, p in enumerate(refs.image_paths, start=1):
            name = f"ref_{actual_seed}_{i}{p.suffix.lower() or '.png'}"
            ref_names.append(name)
            images_payload.append(
                {"name": name, "image": base64.b64encode(p.read_bytes()).decode("ascii")}
            )
        wf = cap._patch_flux_workflow(
            bundle,
            prompt_text=prompt_text,
            lora_name=lora_name,
            lora_strength=float(settings.flux_lora_strength),
            ref_uploaded_names=ref_names,
            seed=actual_seed,
        )
        payload = {"input": {"workflow": wf, "images": images_payload}}
        log.info(
            "runpod_flux_lora_submit",
            lora=lora_name,
            refs=len(ref_names),
            prompt_preview=prompt_text[:80],
        )
        job_id = _submit_job(self._cfg, payload)
        result = _poll_until_complete(self._cfg, job_id)
        output = result.get("output")
        if output is None:
            raise RunPodError("RunPod flux lora /status COMPLETED but output is null", detail=result)
        png_bytes = _decode_runpod_output(output, expected_ext=".png")
        out_path.write_bytes(png_bytes)
        self.verify_png(out_path, min_w=bundle.min_width, min_h=bundle.min_height)
        log.info(
            "runpod_flux_lora_done",
            out=out_path.name,
            size_kb=int(out_path.stat().st_size / 1024),
        )

    def generate_flux_identity_one(
        self,
        bundle: Any,
        prompt_text: str,
        out_path: Path,
        *,
        refs: Any,
        settings: Settings,
        seed: int | None = None,
    ) -> None:
        from shorts_pipeline.image_worker.face_mask import build_face_inpaint_mask
        from shorts_pipeline.image_worker.flux_identity_generator import (
            FluxIdentityBundle,
            PORTRAIT_H,
            PORTRAIT_W,
            _FluxIdentityCapable,
        )

        if not isinstance(bundle, FluxIdentityBundle):
            raise RunPodError("generate_flux_identity_one requires FluxIdentityBundle")
        cap = _FluxIdentityCapable()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        actual_seed = seed if seed is not None else random.randint(1, 2**32 - 1)
        images_payload: list[dict[str, str]] = []
        ref_names: list[str] = []
        for i, p in enumerate(refs.image_paths, start=1):
            name = f"ref_{actual_seed}_{i}{p.suffix.lower() or '.png'}"
            ref_names.append(name)
            images_payload.append(
                {"name": name, "image": base64.b64encode(p.read_bytes()).decode("ascii")}
            )
        mask_path = build_face_inpaint_mask(
            refs.image_paths[0],
            width=PORTRAIT_W,
            height=PORTRAIT_H,
        )
        mask_name = f"face_mask_{actual_seed}.png"
        images_payload.append(
            {"name": mask_name, "image": base64.b64encode(mask_path.read_bytes()).decode("ascii")}
        )
        wf = cap._patch_identity_workflow(
            bundle,
            prompt_text=prompt_text,
            ref_uploaded_names=ref_names,
            face_mask_name=mask_name,
            settings=settings,
            seed=actual_seed,
        )
        payload = {"input": {"workflow": wf, "images": images_payload}}
        log.info(
            "runpod_flux_identity_submit",
            refs=len(ref_names),
            prompt_preview=prompt_text[:80],
        )
        job_id = _submit_job(self._cfg, payload)
        result = _poll_until_complete(self._cfg, job_id)
        output = result.get("output")
        if output is None:
            raise RunPodError("RunPod flux identity /status COMPLETED but output is null", detail=result)
        png_bytes = _decode_runpod_output(output, expected_ext=".png")
        out_path.write_bytes(png_bytes)
        self.verify_png(out_path, min_w=bundle.min_width, min_h=bundle.min_height)
        log.info(
            "runpod_flux_identity_done",
            out=out_path.name,
            size_kb=int(out_path.stat().st_size / 1024),
        )

    def verify_png(self, path: Path, *, min_w: int, min_h: int) -> None:
        """Match ComfyClient.verify_png so disk recovery works in either transport mode."""
        try:
            with Image.open(path) as im:
                im.verify()
            with Image.open(path) as im2:
                w, h = im2.size
        except Exception as e:
            raise RunPodError(f"invalid PNG: {path}: {e}") from e
        if w < min_w or h < min_h:
            raise RunPodError(f"PNG too small: {w}x{h} < {min_w}x{min_h}")


# ── Wan I2V client ────────────────────────────────────────────────────────────


class RunPodWanI2VClient:
    """RunPod-backed image-to-video. Mirrors WanI2VClient.generate_clip()."""

    def __init__(self, settings: Settings) -> None:
        self._s = settings
        self._cfg = _RunPodConfig.from_settings(settings)

    def generate_clip(
        self,
        bundle: WanI2VBundle,
        *,
        image_path: Path,
        motion_prompt: str,
        duration_s: float,
        out_path: Path,
        seed: int | None = None,
    ) -> None:
        if not image_path.exists():
            raise WanI2VError(f"source image does not exist: {image_path}")
        if not motion_prompt or len(motion_prompt) < 10:
            raise WanI2VError(
                f"motion_prompt too short ({len(motion_prompt)} chars). "
                "Use video_worker.motion_templates.default_motion_prompt() for fallback."
            )

        out_path.parent.mkdir(parents=True, exist_ok=True)
        frames = duration_to_frames(duration_s)
        actual_seed = seed if seed is not None else random.randint(1, 2**32 - 1)

        # Patch workflow with motion prompt, image filename, length, seeds.
        # The image is uploaded inline (base64) — we tell the workflow to look
        # for the same filename the worker will save to its input/ folder.
        wf = copy.deepcopy(bundle.prompt)
        # RunPod worker takes images as {"name": ..., "image": base64} and writes
        # them to ComfyUI's input/ folder under <name>. The LoadImage node in
        # the workflow must point at this same <name>.
        uploaded_name = f"input_{actual_seed}.png"
        _nested_set(wf, bundle.prompt_key_path, motion_prompt)
        _nested_set(wf, bundle.image_key_path, uploaded_name)
        _nested_set(wf, bundle.length_key_path, frames)
        _patch_seeds(wf, actual_seed)

        image_b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
        payload = {
            "input": {
                "workflow": wf,
                "images": [{"name": uploaded_name, "image": image_b64}],
            }
        }
        log.info(
            "runpod_i2v_submit",
            image=image_path.name,
            duration_s=round(duration_s, 2),
            frames=frames,
            seed=actual_seed,
            prompt_preview=motion_prompt[:80],
        )
        job_id = _submit_job(self._cfg, payload)
        result = _poll_until_complete(self._cfg, job_id)

        output = result.get("output")
        if output is None:
            raise RunPodError("RunPod I2V /status COMPLETED but output is null", detail=result)
        mp4_bytes = _decode_runpod_output(output, expected_ext=".mp4")
        out_path.write_bytes(mp4_bytes)

        size_kb = int(out_path.stat().st_size / 1024)
        if size_kb < 50:
            raise RunPodError(
                f"RunPod I2V output suspiciously small ({size_kb} KB)",
                detail={"job_id": job_id, "frames": frames},
            )
        log.info(
            "runpod_i2v_done",
            out=out_path.name,
            size_kb=size_kb,
            frames=frames,
            exec_ms=result.get("executionTime"),
            delay_ms=result.get("delayTime"),
        )


class RunPodLtxI2VClient:
    """RunPod-backed LTX 2.3 I2V. Mirrors LtxI2VClient.generate_clip()."""

    def __init__(self, settings: Settings) -> None:
        self._s = settings
        self._cfg = _RunPodConfig.from_settings(settings)

    def generate_clip(
        self,
        bundle: Any,
        *,
        image_path: Path,
        motion_prompt: str,
        duration_s: float,
        out_path: Path,
        seed: int | None = None,
    ) -> None:
        from shorts_pipeline.video_worker.ltx_i2v import (
            LtxI2VBundle,
            LtxI2VError,
            duration_to_ltx_seconds,
        )

        if not isinstance(bundle, LtxI2VBundle):
            raise RunPodError("RunPodLtxI2VClient requires LtxI2VBundle")
        if not image_path.exists():
            raise LtxI2VError(f"source image does not exist: {image_path}")
        if not motion_prompt or len(motion_prompt) < 10:
            raise LtxI2VError(f"motion_prompt too short ({len(motion_prompt)} chars)")

        out_path.parent.mkdir(parents=True, exist_ok=True)
        length_s = duration_to_ltx_seconds(duration_s)
        actual_seed = seed if seed is not None else random.randint(1, 2**32 - 1)
        uploaded_name = f"input_{actual_seed}.png"

        wf = copy.deepcopy(bundle.prompt)
        _nested_set(wf, bundle.prompt_key_path, motion_prompt)
        _nested_set(wf, bundle.image_key_path, uploaded_name)
        _nested_set(wf, bundle.length_key_path, length_s)
        _patch_seeds(wf, actual_seed)
        for sk in bundle.seed_key_paths:
            _nested_set(wf, sk, actual_seed)
        if bundle.ltx_audio_switch_key_path:
            _nested_set(wf, bundle.ltx_audio_switch_key_path, False)

        image_b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
        payload = {
            "input": {
                "workflow": wf,
                "images": [{"name": uploaded_name, "image": image_b64}],
            }
        }
        log.info(
            "runpod_ltx_i2v_submit",
            image=image_path.name,
            length_s=length_s,
            seed=actual_seed,
            prompt_preview=motion_prompt[:80],
        )
        job_id = _submit_job(self._cfg, payload)
        result = _poll_until_complete(self._cfg, job_id)
        output = result.get("output")
        if output is None:
            raise RunPodError("RunPod LTX I2V /status COMPLETED but output is null", detail=result)
        mp4_bytes = _decode_runpod_output(output, expected_ext=".mp4")
        out_path.write_bytes(mp4_bytes)
        size_kb = int(out_path.stat().st_size / 1024)
        if size_kb < 50:
            raise RunPodError(
                f"RunPod LTX I2V output suspiciously small ({size_kb} KB)",
                detail={"job_id": job_id, "length_s": length_s},
            )
        log.info(
            "runpod_ltx_i2v_done",
            out=out_path.name,
            size_kb=size_kb,
            length_s=length_s,
            exec_ms=result.get("executionTime"),
            delay_ms=result.get("delayTime"),
        )


# ── Factory used by orchestrator (call sites untouched) ───────────────────────


def make_image_client(settings: Settings) -> Any:
    """Return ComfyClient or RunPodComfyClient based on settings.comfy_mode.

    Orchestrator code should call this instead of `ComfyClient(settings)`
    directly. Both returned objects expose `.generate_one(bundle, prompt, out)`.
    """
    mode = (settings.comfy_mode or "local").lower()
    if mode == "runpod":
        return RunPodComfyClient(settings)
    if mode == "local":
        from shorts_pipeline.image_worker.comfy import ComfyClient
        return ComfyClient(settings)
    raise ComfyError(f"unknown comfy_mode={mode!r} (expected 'local' or 'runpod')")


def make_i2v_client(settings: Settings) -> Any:
    """Return I2V client for configured backend (wan or ltx) and comfy_mode."""
    backend = (settings.i2v_backend or "wan").lower().strip()
    mode = (settings.comfy_mode or "local").lower()
    if backend == "ltx":
        if mode == "runpod":
            return RunPodLtxI2VClient(settings)
        from shorts_pipeline.video_worker.ltx_i2v import LtxI2VClient
        return LtxI2VClient(settings)
    if mode == "runpod":
        return RunPodWanI2VClient(settings)
    if mode == "local":
        from shorts_pipeline.video_worker.wan_i2v import WanI2VClient
        return WanI2VClient(settings)
    raise ComfyError(f"unknown comfy_mode={mode!r} (expected 'local' or 'runpod')")
