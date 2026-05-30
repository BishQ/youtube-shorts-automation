"""ComfyUI HTTP client: queue, poll, upload, upscale, verify PNG outputs."""

from __future__ import annotations

import copy
import json
import random
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from PIL import Image

from shorts_pipeline.config.settings import Settings
from shorts_pipeline.logging_setup import get_logger

log = get_logger(__name__)


class ComfyError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None, detail: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail

    def __str__(self) -> str:
        base = super().__str__()
        if not self.detail:
            return base
        if isinstance(self.detail, str):
            snippet = self.detail.strip()
        else:
            snippet = str(self.detail).strip()
        if not snippet:
            return base
        if len(snippet) > 500:
            snippet = snippet[:500] + "..."
        return f"{base}: {snippet}"


# ── Workflow bundle types ─────────────────────────────────────────────────────

@dataclass
class WorkflowBundle:
    """Generation workflow: has a positive-prompt injection path."""
    prompt: dict[str, Any]
    prompt_key_path: list[str | int]
    min_width: int
    min_height: int


@dataclass
class UpscaleBundle:
    """Upscale-only workflow: injects an uploaded image filename."""
    prompt: dict[str, Any]
    image_key_path: list[str | int]
    min_width: int
    min_height: int


# ── Loaders ───────────────────────────────────────────────────────────────────

def load_workflow_bundle(path: Path) -> WorkflowBundle:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ComfyError("workflow file must be a JSON object")
    meta = raw.get("meta") or {}
    prompt = raw.get("prompt")
    if not isinstance(prompt, dict) or not prompt:
        raise ComfyError(
            "workflow JSON must include non-empty 'prompt' (Comfy API export). "
            "Use ComfyUI 'Save (API Format)' and wrap as {\"meta\": {...}, \"prompt\": {...} }."
        )
    pk = meta.get("prompt_key_path")
    if not isinstance(pk, list) or not pk:
        raise ComfyError("meta.prompt_key_path must be a non-empty list of keys, e.g. ['6','inputs','text']")
    mw = int(meta.get("min_width", 512))
    mh = int(meta.get("min_height", 512))
    return WorkflowBundle(prompt=prompt, prompt_key_path=pk, min_width=mw, min_height=mh)


def load_upscale_bundle(path: Path) -> UpscaleBundle:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ComfyError("upscale workflow file must be a JSON object")
    meta = raw.get("meta") or {}
    prompt = raw.get("prompt")
    if not isinstance(prompt, dict) or not prompt:
        raise ComfyError(
            "upscale workflow JSON must include non-empty 'prompt'. "
            "See workflows/upscale_4x_ultrasharp.json for the expected format."
        )
    ik = meta.get("image_key_path")
    if not isinstance(ik, list) or not ik:
        raise ComfyError(
            "upscale workflow meta.image_key_path must be a non-empty list, "
            "e.g. ['1','inputs','image'] pointing to the LoadImage node."
        )
    mw = int(meta.get("min_width", 1080))
    mh = int(meta.get("min_height", 1920))
    return UpscaleBundle(prompt=prompt, image_key_path=ik, min_width=mw, min_height=mh)


# ── Shared helpers ────────────────────────────────────────────────────────────

LOCKED_NEGATIVE_PROMPT = (
    "blurry, deformed, ugly, disfigured, bad anatomy, bad proportions, extra limbs, "
    "missing limbs, mutated hands, fused fingers, extra fingers, malformed face, "
    "asymmetric face, cross eyed, weird eyes, double face, two heads, "
    "watermark, signature, text, letters, words, numbers, captions, title, label, "
    "logo, subtitle, caption, banner, speech bubble, "
    "low quality, lowres, jpeg artifacts, compression artifacts, "
    "cartoon, anime, manga, illustration, painting style, 3d render, cgi, "
    "plastic skin, doll skin, airbrushed skin, oversaturated, neon colors, "
    "instagram filter, vintage filter, oversharpened, grainy, motion blur, out of focus, "
    "modern clothing, t-shirt, jeans, suit and tie, sneakers, sunglasses, hoodie, "
    "baseball cap, wristwatch, smartphone, phone, laptop, computer, tablet, "
    "car, bicycle, motorcycle, electricity wires, power lines, "
    "neon signs, modern buildings, skyscraper, glass curtain wall, "
    "plastic objects, anachronism, futuristic, sci-fi, robot, cyberpunk, "
    "fantasy creature, generic stock photo, selfie, amateur photography, "
    "flat lighting, harsh flash, ring light, studio backdrop, swastika, "
    "nazi insignia, specific flag symbols, offensive symbols"
)


def _nested_set(d: dict[str, Any], path: list[str | int], value: Any) -> None:
    cur: Any = d
    for i, key in enumerate(path):
        last = i == len(path) - 1
        if last:
            cur[key] = value
        else:
            if key not in cur or not isinstance(cur[key], dict):
                raise ComfyError(f"invalid prompt_key_path at segment {key!r}")
            cur = cur[key]


def _patch_workflow(wf: dict[str, Any], positive_text: str, seed: int) -> dict[str, Any]:
    """Lock the negative prompt and randomize seed in every KSampler node."""
    for node in wf.values():
        if not isinstance(node, dict):
            continue
        title = (node.get("_meta") or {}).get("title", "").upper()
        inputs = node.get("inputs") or {}
        if "NEGATIVE" in title and "text" in inputs:
            inputs["text"] = LOCKED_NEGATIVE_PROMPT
        ct = node.get("class_type", "")
        if ct in ("KSampler", "KSamplerAdvanced", "SamplerCustom") and "seed" in inputs:
            inputs["seed"] = seed
    return wf


def _pick_last_png(outputs: dict[str, Any]) -> tuple[str, str]:
    """Return (filename, subfolder) of the LAST SaveImage PNG in the outputs dict.

    Iterating all outputs and keeping the last PNG means multi-stage workflows
    (e.g. Stage1 → Stage2 → Upscale each with a SaveImage) naturally return the
    highest-quality final output instead of the first intermediate one.
    """
    png_name: str | None = None
    subfolder = ""
    for node_out in outputs.values():
        for img in (node_out or {}).get("images", []):
            fn = img.get("filename")
            if isinstance(fn, str) and fn.lower().endswith(".png"):
                png_name = fn
                subfolder = str(img.get("subfolder") or "")
    if not png_name:
        raise ComfyError(
            "Comfy history missing PNG output",
            detail={"outputs_keys": list(outputs.keys())},
        )
    return png_name, subfolder


# ── ComfyClient ───────────────────────────────────────────────────────────────

class ComfyClient:
    def __init__(self, settings: Settings) -> None:
        self._s = settings

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        base = self._s.comfy_base_url.rstrip("/")
        url = f"{base}{path}"
        interval = max(0.5, float(self._s.comfy_connect_retry_interval_s))
        max_wait = float(self._s.comfy_connect_max_wait_s)
        deadline = time.monotonic() + max_wait if max_wait > 0 else None
        attempt = 0
        last_log = 0.0
        while True:
            try:
                with httpx.Client(timeout=self._s.comfy_timeout_s) as client:
                    return client.request(method, url, **kwargs)
            except (httpx.ConnectError, httpx.ConnectTimeout) as e:
                attempt += 1
                if deadline is not None and time.monotonic() >= deadline:
                    raise ComfyError(
                        f"Comfy unreachable for {max_wait:.0f}s while calling {path}: {e}",
                        detail={"attempts": attempt},
                    ) from e
                now = time.monotonic()
                if attempt == 1 or now - last_log >= 25.0:
                    last_log = now
                    log.warning(
                        "comfy_waiting_for_server",
                        path=path,
                        base_url=base,
                        attempt=attempt,
                        sleep_s=round(interval, 2),
                        error=str(e),
                    )
                time.sleep(interval)
                interval = min(20.0, interval * 1.08)
            except httpx.TimeoutException as e:
                raise ComfyError(f"Comfy timeout calling {path}: {e}") from e
            except httpx.RequestError as e:
                raise ComfyError(f"Comfy connection error calling {path}: {e}") from e

    def queue_prompt(self, workflow: dict[str, Any]) -> str:
        client_id = str(uuid.uuid4())
        payload = {"prompt": workflow, "client_id": client_id}
        r = self._request("POST", "/prompt", json=payload)
        if r.status_code >= 500:
            raise ComfyError(
                f"Comfy server error HTTP {r.status_code}",
                status_code=r.status_code,
                detail=r.text[:2000],
            )
        if r.status_code >= 400:
            detail = r.text[:2000]
            log.error("comfy_prompt_rejected", status_code=r.status_code, detail=detail[:800])
            raise ComfyError(
                f"Comfy client error HTTP {r.status_code}",
                status_code=r.status_code,
                detail=detail,
            )
        data = r.json()
        pid = data.get("prompt_id")
        if not isinstance(pid, str):
            raise ComfyError("Comfy /prompt missing prompt_id", detail=data)
        return pid

    def get_history(self, prompt_id: str) -> dict[str, Any] | None:
        r = self._request("GET", f"/history/{prompt_id}")
        if r.status_code == 404:
            return self._history_from_full_dump(prompt_id)
        if r.status_code >= 500:
            raise ComfyError(
                f"Comfy history 5xx HTTP {r.status_code}",
                status_code=r.status_code,
                detail=r.text[:2000],
            )
        if r.status_code >= 400:
            raise ComfyError(
                f"Comfy history error HTTP {r.status_code}",
                status_code=r.status_code,
                detail=r.text[:2000],
            )
        data = r.json()
        if not isinstance(data, dict):
            return None
        entry = data.get(prompt_id)
        if entry is None and prompt_id in data:
            entry = data[prompt_id]
        if entry is None:
            return self._history_from_full_dump(prompt_id)
        return entry  # type: ignore[no-any-return]

    def _history_from_full_dump(self, prompt_id: str) -> dict[str, Any] | None:
        r = self._request("GET", "/history")
        if r.status_code >= 500:
            raise ComfyError(
                f"Comfy /history 5xx HTTP {r.status_code}",
                status_code=r.status_code,
            )
        if r.status_code >= 400:
            return None
        data = r.json()
        if not isinstance(data, dict):
            return None
        entry = data.get(prompt_id)
        return entry if isinstance(entry, dict) else None

    def wait_for_completion(self, prompt_id: str) -> dict[str, Any]:
        for _ in range(self._s.comfy_max_polls):
            h = self.get_history(prompt_id)
            if h and h.get("outputs"):
                return h
            status_r = self._request("GET", "/queue")
            if status_r.status_code >= 500:
                raise ComfyError(
                    f"Comfy queue 5xx HTTP {status_r.status_code}",
                    status_code=status_r.status_code,
                )
            time.sleep(self._s.comfy_poll_interval_s)
        raise ComfyError(
            f"Comfy prompt {prompt_id} did not complete within "
            f"{self._s.comfy_max_polls * self._s.comfy_poll_interval_s}s"
        )

    def fetch_output_png(self, filename: str, subfolder: str = "", folder_type: str = "output") -> bytes:
        params = {"filename": filename, "type": folder_type}
        if subfolder:
            params["subfolder"] = subfolder
        r = self._request("GET", "/view", params=params)
        if r.status_code >= 500:
            raise ComfyError(
                f"Comfy /view 5xx HTTP {r.status_code}",
                status_code=r.status_code,
            )
        if r.status_code >= 400:
            raise ComfyError(
                f"Comfy /view error HTTP {r.status_code}",
                status_code=r.status_code,
                detail=r.text[:500],
            )
        return r.content

    def verify_png(self, path: Path, *, min_w: int, min_h: int) -> None:
        try:
            with Image.open(path) as im:
                im.verify()
            with Image.open(path) as im2:
                w, h = im2.size
        except Exception as e:
            raise ComfyError(f"corrupt or unreadable PNG: {path}: {e}") from e
        if w < min_w or h < min_h:
            raise ComfyError(f"image too small: {w}x{h} < {min_w}x{min_h}: {path}")

    # ── Upload ────────────────────────────────────────────────────────────────

    def upload_image(self, path: Path) -> str:
        """Upload a local image to ComfyUI /upload/image; return the server filename.

        ComfyUI stores uploaded images in its input folder under the returned
        name.  Pass that name to a LoadImage node's 'image' input.
        """
        image_bytes = path.read_bytes()
        r = self._request(
            "POST",
            "/upload/image",
            files={"image": (path.name, image_bytes, "image/png")},
            data={"type": "input", "overwrite": "true"},
        )
        if r.status_code >= 400:
            raise ComfyError(
                f"Comfy /upload/image HTTP {r.status_code}",
                status_code=r.status_code,
                detail=r.text[:2000],
            )
        resp = r.json()
        name = resp.get("name")
        if not isinstance(name, str):
            raise ComfyError("Comfy /upload/image missing 'name' in response", detail=resp)
        return name

    # ── Single-image generation ───────────────────────────────────────────────

    def generate_one(
        self,
        bundle: WorkflowBundle,
        prompt_text: str,
        out_path: Path,
        *,
        seed: int | None = None,
    ) -> None:
        """Generate a single image and write it to out_path."""
        out_path.parent.mkdir(parents=True, exist_ok=True)
        wf = copy.deepcopy(bundle.prompt)
        _nested_set(wf, bundle.prompt_key_path, prompt_text)
        actual_seed = seed if seed is not None else random.randint(1, 2**32 - 1)
        _patch_workflow(wf, prompt_text, actual_seed)
        pid = self.queue_prompt(wf)
        hist = self.wait_for_completion(pid)
        outputs = hist.get("outputs") or {}
        png_name, subfolder = _pick_last_png(outputs)
        data = self.fetch_output_png(png_name, subfolder=subfolder)
        out_path.write_bytes(data)
        self.verify_png(out_path, min_w=bundle.min_width, min_h=bundle.min_height)

    # ── Upscale ───────────────────────────────────────────────────────────────

    def upscale_image(
        self,
        bundle: UpscaleBundle,
        input_path: Path,
        out_path: Path,
    ) -> None:
        """Upload input_path to ComfyUI, run the upscale workflow, save to out_path."""
        uploaded_name = self.upload_image(input_path)
        log.info(
            "comfy_upscale_start",
            input=input_path.name,
            uploaded_as=uploaded_name,
            out=out_path.name,
        )
        wf = copy.deepcopy(bundle.prompt)
        _nested_set(wf, bundle.image_key_path, uploaded_name)
        pid = self.queue_prompt(wf)
        hist = self.wait_for_completion(pid)
        outputs = hist.get("outputs") or {}
        png_name, subfolder = _pick_last_png(outputs)
        data = self.fetch_output_png(png_name, subfolder=subfolder)
        out_path.write_bytes(data)
        self.verify_png(out_path, min_w=bundle.min_width, min_h=bundle.min_height)
        log.info("comfy_upscale_done", out=out_path.name, size_kb=int(out_path.stat().st_size / 1024))

    # ── Batch generation (all images through one workflow) ────────────────────

    def generate_images_for_prompts(
        self,
        bundle: WorkflowBundle,
        prompts: list[str],
        out_dir: Path,
        *,
        expected_count: int | None = None,
        progress_cb: Any = None,
    ) -> list[Path]:
        out_dir.mkdir(parents=True, exist_ok=True)
        expected = expected_count if expected_count is not None else len(prompts)
        if len(prompts) != expected:
            raise ComfyError(f"prompt count mismatch: {len(prompts)} != expected {expected}")

        paths: list[Path] = []
        for i, ptxt in enumerate(prompts):
            log.info("image_generating", index=i + 1, total=len(prompts), prompt_preview=ptxt[:80])
            out_path = out_dir / f"clause_{i:03d}.png"
            self.generate_one(bundle, ptxt, out_path)
            paths.append(out_path)
            if progress_cb:
                progress_cb(i, ptxt)

        if len(paths) != expected:
            raise ComfyError(
                f"partial image set: got {len(paths)} paths, expected {expected}",
                detail={"paths": [str(p) for p in paths]},
            )
        return paths

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
            generate_flux_lora_one,
        )

        if not isinstance(bundle, FluxLoraBundle):
            raise ComfyError("generate_flux_lora_one requires FluxLoraBundle")
        generate_flux_lora_one(
            self,
            bundle,
            prompt_text,
            out_path,
            refs=refs,
            settings=settings,
            seed=seed,
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
        from shorts_pipeline.image_worker.flux_identity_generator import (
            FluxIdentityBundle,
            generate_flux_identity_one,
        )

        if not isinstance(bundle, FluxIdentityBundle):
            raise ComfyError("generate_flux_identity_one requires FluxIdentityBundle")
        generate_flux_identity_one(
            self,
            bundle,
            prompt_text,
            out_path,
            refs=refs,
            settings=settings,
            seed=seed,
        )
