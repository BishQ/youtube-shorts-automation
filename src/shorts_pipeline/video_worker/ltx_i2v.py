"""LTX 2.3 image-to-video ComfyUI client (dev BF16 full safetensors workflow)."""

from __future__ import annotations

import copy
import json
import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shorts_pipeline.config.settings import Settings
from shorts_pipeline.image_worker.comfy import ComfyClient, ComfyError, _nested_set
from shorts_pipeline.logging_setup import get_logger
from shorts_pipeline.video_worker.wan_i2v import _patch_seeds, _pick_last_mp4

log = get_logger(__name__)

LTX_FPS = 24
LTX_MIN_SECONDS = 2.0
LTX_MAX_SECONDS = 10.0


class LtxI2VError(ComfyError):
    """Raised on LTX 2.3 I2V-specific failures."""


@dataclass
class LtxI2VBundle:
    prompt: dict[str, Any]
    prompt_key_path: list[str | int]
    image_key_path: list[str | int]
    length_key_path: list[str | int]
    seed_key_paths: list[list[str | int]]
    min_width: int
    min_height: int
    ltx_audio_switch_key_path: list[str | int] | None = None


def load_ltx_i2v_bundle(path: Path) -> LtxI2VBundle:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise LtxI2VError("LTX I2V workflow must be a JSON object")
    meta = raw.get("meta") or {}
    prompt = raw.get("prompt")
    if not isinstance(prompt, dict) or not prompt:
        raise LtxI2VError("LTX workflow requires non-empty 'prompt' dict")
    for name in ("prompt_key_path", "image_key_path", "length_key_path"):
        val = meta.get(name)
        if not isinstance(val, list) or not val:
            raise LtxI2VError(f"LTX workflow meta.{name} must be a non-empty list")
    seed_paths = meta.get("seed_key_paths") or meta.get("seed_key_path")
    if isinstance(seed_paths, list) and seed_paths and isinstance(seed_paths[0], list):
        sk: list[list[str | int]] = seed_paths
    elif isinstance(seed_paths, list) and seed_paths:
        sk = [seed_paths]
    else:
        sk = []
    audio_sw = meta.get("ltx_audio_switch_key_path")
    if isinstance(audio_sw, list) and audio_sw:
        ask: list[str | int] | None = audio_sw
    else:
        ask = None
    return LtxI2VBundle(
        prompt=prompt,
        prompt_key_path=meta["prompt_key_path"],
        image_key_path=meta["image_key_path"],
        length_key_path=meta["length_key_path"],
        seed_key_paths=sk,
        min_width=int(meta.get("min_width", 480)),
        min_height=int(meta.get("min_height", 832)),
        ltx_audio_switch_key_path=ask,
    )


def duration_to_ltx_seconds(seconds: float) -> int:
    """Clamp clause duration to LTX-friendly whole seconds."""
    if seconds <= 0:
        return int(LTX_MIN_SECONDS)
    clamped = max(LTX_MIN_SECONDS, min(LTX_MAX_SECONDS, seconds))
    return max(2, int(math.ceil(clamped)))


class LtxI2VClient:
    """Run LTX 2.3 I2V via local / RunPod-proxy ComfyUI."""

    def __init__(self, settings: Settings) -> None:
        self._s = settings
        self._comfy = ComfyClient(settings)

    def generate_clip(
        self,
        bundle: LtxI2VBundle,
        *,
        image_path: Path,
        motion_prompt: str,
        duration_s: float,
        out_path: Path,
        seed: int | None = None,
    ) -> None:
        if not image_path.exists():
            raise LtxI2VError(f"source image does not exist: {image_path}")
        if not motion_prompt or len(motion_prompt) < 10:
            raise LtxI2VError(f"motion_prompt too short ({len(motion_prompt)} chars)")

        out_path.parent.mkdir(parents=True, exist_ok=True)
        length_s = duration_to_ltx_seconds(duration_s)
        actual_seed = seed if seed is not None else random.randint(1, 2**32 - 1)
        uploaded_name = self._comfy.upload_image(image_path)

        wf = copy.deepcopy(bundle.prompt)
        _nested_set(wf, bundle.prompt_key_path, motion_prompt)
        _nested_set(wf, bundle.image_key_path, uploaded_name)
        _nested_set(wf, bundle.length_key_path, length_s)
        _patch_seeds(wf, actual_seed)
        for sk in bundle.seed_key_paths:
            _nested_set(wf, sk, actual_seed)
        if bundle.ltx_audio_switch_key_path:
            # false → LTX-generated audio (no external mp3 required on the pod).
            _nested_set(wf, bundle.ltx_audio_switch_key_path, False)

        log.info(
            "ltx_i2v_start",
            image=image_path.name,
            uploaded_as=uploaded_name,
            length_s=length_s,
            out=out_path.name,
            prompt_preview=motion_prompt[:80],
        )
        pid = self._comfy.queue_prompt(wf)
        hist = self._comfy.wait_for_completion(pid)
        outputs = hist.get("outputs") or {}
        video_name, subfolder = _pick_last_mp4(outputs)
        data = self._comfy.fetch_output_png(video_name, subfolder=subfolder)
        out_path.write_bytes(data)

        size_kb = int(out_path.stat().st_size / 1024)
        if size_kb < 50:
            raise LtxI2VError(
                f"LTX I2V output suspiciously small ({size_kb} KB): {out_path}",
                detail={"video_name": video_name, "length_s": length_s},
            )
        log.info("ltx_i2v_done", out=out_path.name, size_kb=size_kb, length_s=length_s)
