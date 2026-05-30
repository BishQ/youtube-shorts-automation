"""Wan 2.2 I2V-A14B ComfyUI client — image + motion prompt -> MP4 clip.

Pipeline per clause:
  1. Upload the Qwen-generated PNG to ComfyUI's input folder (/upload/image).
  2. Patch the workflow:
       - motion prompt text into the positive CLIPTextEncode node
       - uploaded filename into the LoadImage node
       - frame count (length) on WanImageToVideo (rounded from clause duration * fps)
       - randomized seeds in both KSamplerAdvanced nodes
  3. Queue prompt, poll history, locate the SaveVideo/VHS_VideoCombine output.
  4. Fetch the MP4 bytes and write to disk.

Design mirrors `image_worker.comfy.ComfyClient` so it reuses the same HTTP retry
loop, history polling, and upload helper. We intentionally do NOT inherit —
composition keeps the I2V-specific quirks (video output discovery, length
injection, frame budget math) localised here.
"""

from __future__ import annotations

import copy
import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shorts_pipeline.config.settings import Settings
from shorts_pipeline.image_worker.comfy import (
    ComfyClient,
    ComfyError,
    _nested_set,
)
from shorts_pipeline.logging_setup import get_logger

log = get_logger(__name__)


class WanI2VError(ComfyError):
    """Raised on Wan I2V-specific failures (still a ComfyError subclass)."""


# Wan 2.2 was trained at 16fps; deviating drops quality.
WAN_FPS = 16
# Wan native sequence is 81 frames (~5s). Hard cap to avoid quality degradation.
WAN_MIN_FRAMES = 33   # ~2.0s — anything shorter and the model can't establish motion
# 121 = 4×30+1 → ~7.5s, the hook's HOOK_MAX_DURATION_S slot. Wan coherence softens
# past ~7s; this +0.5s margin exists only so the renderer never runs out of frames
# to trim for a 7.5s hook. Dial back to 113 (~7.0s) if hook clips warp/ghost.
WAN_MAX_FRAMES = 121  # ~7.5s


@dataclass
class WanI2VBundle:
    """Workflow bundle for Wan I2V.

    Three injection points (vs the still-image bundle's one):
      - prompt_key_path  → positive CLIPTextEncode text
      - image_key_path   → LoadImage 'image' filename (uploaded name)
      - length_key_path  → WanImageToVideo 'length' (frame count)
    """
    prompt: dict[str, Any]
    prompt_key_path: list[str | int]
    image_key_path: list[str | int]
    length_key_path: list[str | int]
    min_width: int
    min_height: int


@dataclass(frozen=True)
class WanClipRequest:
    image_path: Path
    motion_prompt: str
    duration_s: float
    out_path: Path
    seed: int | None = None


def load_i2v_bundle(path: Path) -> WanI2VBundle:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise WanI2VError("I2V workflow file must be a JSON object")
    meta = raw.get("meta") or {}
    prompt = raw.get("prompt")
    if not isinstance(prompt, dict) or not prompt:
        raise WanI2VError(
            "I2V workflow JSON must include non-empty 'prompt' (Comfy API export). "
            "See workflows/wan22_i2v_a14b.json for the expected shape."
        )
    pk = meta.get("prompt_key_path")
    ik = meta.get("image_key_path")
    lk = meta.get("length_key_path")
    for name, val in (("prompt_key_path", pk), ("image_key_path", ik), ("length_key_path", lk)):
        if not isinstance(val, list) or not val:
            raise WanI2VError(f"I2V workflow meta.{name} must be a non-empty list of keys")
    return WanI2VBundle(
        prompt=prompt,
        prompt_key_path=pk,
        image_key_path=ik,
        length_key_path=lk,
        min_width=int(meta.get("min_width", 720)),
        min_height=int(meta.get("min_height", 1280)),
    )


def duration_to_frames(seconds: float) -> int:
    """Round a clause duration to a Wan-friendly frame count.

    Wan 2.2 trains on multiples of 4 frames (latent compression). We round to
    the nearest multiple of 4 then clamp to [WAN_MIN_FRAMES, WAN_MAX_FRAMES].
    """
    if seconds <= 0:
        return WAN_MIN_FRAMES
    raw = round(seconds * WAN_FPS)
    # Nearest multiple of 4 (latent-friendly)
    snapped = max(4, (raw + 2) // 4 * 4)
    # Plus 1 because Wan uses (4n + 1) frame counts internally (49, 81, 97 etc.)
    snapped = snapped + 1
    return max(WAN_MIN_FRAMES, min(WAN_MAX_FRAMES, snapped))


def _patch_seeds(wf: dict[str, Any], seed: int) -> None:
    """Randomize seeds on every KSampler / KSamplerAdvanced / SamplerCustom node."""
    for node in wf.values():
        if not isinstance(node, dict):
            continue
        ct = node.get("class_type", "")
        inputs = node.get("inputs") or {}
        if ct in ("KSampler", "KSamplerAdvanced", "SamplerCustom"):
            if "seed" in inputs:
                inputs["seed"] = seed
            if "noise_seed" in inputs:
                inputs["noise_seed"] = seed


def _pick_last_mp4(outputs: dict[str, Any]) -> tuple[str, str]:
    """Find the last MP4 (or WEBM) in the history outputs.

    VHS_VideoCombine reports the video under either 'gifs' or 'videos' depending
    on the version installed. We scan all output node entries for any file with
    a video extension and take the LAST one (mirrors `_pick_last_png`).
    """
    video_name: str | None = None
    subfolder = ""
    video_exts = (".mp4", ".webm", ".mov", ".mkv")
    for node_out in outputs.values():
        if not isinstance(node_out, dict):
            continue
        for key in ("gifs", "videos", "images"):
            for item in node_out.get(key) or []:
                fn = item.get("filename")
                if isinstance(fn, str) and fn.lower().endswith(video_exts):
                    video_name = fn
                    subfolder = str(item.get("subfolder") or "")
    if not video_name:
        raise WanI2VError(
            "Comfy history missing video output (no MP4/WEBM found)",
            detail={"outputs_keys": list(outputs.keys())},
        )
    return video_name, subfolder


class WanI2VClient:
    """High-level Wan 2.2 I2V runner. Wraps ComfyClient for HTTP."""

    def __init__(self, settings: Settings) -> None:
        self._s = settings
        self._comfy = ComfyClient(settings)

    def _build_workflow(
        self,
        bundle: WanI2VBundle,
        *,
        uploaded_name: str,
        motion_prompt: str,
        duration_s: float,
        seed: int,
    ) -> tuple[dict[str, Any], int]:
        frames = duration_to_frames(duration_s)
        wf = copy.deepcopy(bundle.prompt)
        _nested_set(wf, bundle.prompt_key_path, motion_prompt)
        _nested_set(wf, bundle.image_key_path, uploaded_name)
        _nested_set(wf, bundle.length_key_path, frames)
        _patch_seeds(wf, seed)
        return wf, frames

    def _save_clip_from_history(
        self,
        hist: dict[str, Any],
        out_path: Path,
        *,
        frames: int,
        seed: int,
    ) -> None:
        outputs = hist.get("outputs") or {}
        video_name, subfolder = _pick_last_mp4(outputs)
        data = self._comfy.fetch_output_png(video_name, subfolder=subfolder)
        out_path.write_bytes(data)
        size_kb = int(out_path.stat().st_size / 1024)
        if size_kb < 50:
            raise WanI2VError(
                f"Wan I2V output suspiciously small ({size_kb} KB): {out_path}",
                detail={"video_name": video_name, "frames": frames},
            )
        log.info(
            "wan_i2v_done",
            out=out_path.name,
            size_kb=size_kb,
            frames=frames,
            seed=seed,
        )

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
        self.generate_clips_batched(
            bundle,
            [
                WanClipRequest(
                    image_path=image_path,
                    motion_prompt=motion_prompt,
                    duration_s=duration_s,
                    out_path=out_path,
                    seed=seed,
                )
            ],
        )

    def generate_clips_batched(
        self,
        bundle: WanI2VBundle,
        requests: list[WanClipRequest],
    ) -> None:
        if not requests:
            return
        batch = self._s.comfy_queue_batch and len(requests) > 1
        if not batch:
            for req in requests:
                self._generate_one_serial(bundle, req)
            return

        queued: list[tuple[str, WanClipRequest, int, int]] = []
        prompt_ids: list[str] = []
        for req in requests:
            self._validate_request(req)
            req.out_path.parent.mkdir(parents=True, exist_ok=True)
            actual_seed = req.seed if req.seed is not None else random.randint(1, 2**32 - 1)
            uploaded_name = self._comfy.upload_image(req.image_path)
            wf, frames = self._build_workflow(
                bundle,
                uploaded_name=uploaded_name,
                motion_prompt=req.motion_prompt,
                duration_s=req.duration_s,
                seed=actual_seed,
            )
            log.info(
                "wan_i2v_queued",
                image=req.image_path.name,
                uploaded_as=uploaded_name,
                duration_s=round(req.duration_s, 2),
                frames=frames,
                out=req.out_path.name,
                prompt_preview=req.motion_prompt[:80],
            )
            pid = self._comfy.queue_prompt(wf)
            prompt_ids.append(pid)
            queued.append((pid, req, frames, actual_seed))

        log.info("wan_i2v_batch_queued", count=len(prompt_ids))
        hist_map = self._comfy.wait_for_prompts(prompt_ids)
        for pid, req, frames, actual_seed in queued:
            self._save_clip_from_history(
                hist_map[pid],
                req.out_path,
                frames=frames,
                seed=actual_seed,
            )

    def _validate_request(self, req: WanClipRequest) -> None:
        if not req.image_path.exists():
            raise WanI2VError(f"source image does not exist: {req.image_path}")
        if not req.motion_prompt or len(req.motion_prompt) < 10:
            raise WanI2VError(
                f"motion_prompt too short ({len(req.motion_prompt)} chars). "
                "Use video_worker.motion_templates.default_motion_prompt() for fallback."
            )

    def _generate_one_serial(self, bundle: WanI2VBundle, req: WanClipRequest) -> None:
        self._validate_request(req)
        req.out_path.parent.mkdir(parents=True, exist_ok=True)
        actual_seed = req.seed if req.seed is not None else random.randint(1, 2**32 - 1)
        uploaded_name = self._comfy.upload_image(req.image_path)
        wf, frames = self._build_workflow(
            bundle,
            uploaded_name=uploaded_name,
            motion_prompt=req.motion_prompt,
            duration_s=req.duration_s,
            seed=actual_seed,
        )
        log.info(
            "wan_i2v_start",
            image=req.image_path.name,
            uploaded_as=uploaded_name,
            duration_s=round(req.duration_s, 2),
            frames=frames,
            out=req.out_path.name,
            prompt_preview=req.motion_prompt[:80],
        )
        pid = self._comfy.queue_prompt(wf)
        hist = self._comfy.wait_for_completion(pid)
        self._save_clip_from_history(
            hist,
            req.out_path,
            frames=frames,
            seed=actual_seed,
        )
