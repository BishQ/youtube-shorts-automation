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


@dataclass(frozen=True)
class LtxClipRequest:
    image_path: Path
    motion_prompt: str
    duration_s: float
    out_path: Path
    seed: int | None = None


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


_LTX_EXTERNAL_AUDIO_NODE_IDS = frozenset(
    {"329", "362", "363", "364", "367", "370", "371", "372", "373", "376", "382"}
)


def _apply_ltx_pipeline_patch(wf: dict[str, Any], *, mute_clip_audio: bool) -> None:
    """Prepare LTX workflow for pipeline use (no Sunwood mp3 branch).

    ComfyUI validates every node in the prompt graph before execution, so unused
    LoadAudio / MelBandRoFormer nodes must be removed — not just switched off.

    When ``mute_clip_audio`` is True, VHS exports video-only MP4s. The final Short
    always uses narration.wav + BGM from FFmpeg render (clip audio is ignored there
    too), but mute avoids ambient LTX audio when previewing clause_*.mp4 files.
    """
    if "109" in wf:
        wf["109"].setdefault("inputs", {})["audio_latent"] = ["199", 0]
    if "140" in wf:
        inputs = wf["140"].setdefault("inputs", {})
        if mute_clip_audio:
            inputs.pop("audio", None)
        else:
            inputs["audio"] = ["201", 0]
    for nid in _LTX_EXTERNAL_AUDIO_NODE_IDS:
        wf.pop(nid, None)


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

    def _build_workflow(
        self,
        bundle: LtxI2VBundle,
        *,
        uploaded_name: str,
        motion_prompt: str,
        duration_s: float,
        seed: int,
    ) -> dict[str, Any]:
        length_s = duration_to_ltx_seconds(duration_s)
        wf = copy.deepcopy(bundle.prompt)
        _nested_set(wf, bundle.prompt_key_path, motion_prompt)
        _nested_set(wf, bundle.image_key_path, uploaded_name)
        _nested_set(wf, bundle.length_key_path, length_s)
        _patch_seeds(wf, seed)
        for sk in bundle.seed_key_paths:
            _nested_set(wf, sk, seed)
        _apply_ltx_pipeline_patch(wf, mute_clip_audio=self._s.ltx_i2v_mute_clip_audio)
        return wf

    def _save_clip_from_history(
        self,
        hist: dict[str, Any],
        out_path: Path,
        *,
        length_s: int,
    ) -> None:
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
        self.generate_clips_batched(
            bundle,
            [
                LtxClipRequest(
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
        bundle: LtxI2VBundle,
        requests: list[LtxClipRequest],
    ) -> None:
        if not requests:
            return
        batch = self._s.comfy_queue_batch and len(requests) > 1
        if not batch:
            for req in requests:
                self._generate_one_serial(bundle, req)
            return

        queued: list[tuple[str, LtxClipRequest, int, str]] = []
        prompt_ids: list[str] = []
        for req in requests:
            self._validate_request(bundle, req)
            req.out_path.parent.mkdir(parents=True, exist_ok=True)
            actual_seed = req.seed if req.seed is not None else random.randint(1, 2**32 - 1)
            uploaded_name = self._comfy.upload_image(req.image_path)
            length_s = duration_to_ltx_seconds(req.duration_s)
            wf = self._build_workflow(
                bundle,
                uploaded_name=uploaded_name,
                motion_prompt=req.motion_prompt,
                duration_s=req.duration_s,
                seed=actual_seed,
            )
            log.info(
                "ltx_i2v_queued",
                image=req.image_path.name,
                length_s=length_s,
                out=req.out_path.name,
                prompt_preview=req.motion_prompt[:80],
            )
            pid = self._comfy.queue_prompt(wf)
            prompt_ids.append(pid)
            queued.append((pid, req, length_s, uploaded_name))

        log.info("ltx_i2v_batch_queued", count=len(prompt_ids))
        hist_map = self._comfy.wait_for_prompts(prompt_ids)
        for pid, req, length_s, _uploaded in queued:
            self._save_clip_from_history(hist_map[pid], req.out_path, length_s=length_s)

    def _validate_request(self, bundle: LtxI2VBundle, req: LtxClipRequest) -> None:
        if not req.image_path.exists():
            raise LtxI2VError(f"source image does not exist: {req.image_path}")
        if not req.motion_prompt or len(req.motion_prompt) < 10:
            raise LtxI2VError(f"motion_prompt too short ({len(req.motion_prompt)} chars)")

    def _generate_one_serial(self, bundle: LtxI2VBundle, req: LtxClipRequest) -> None:
        self._validate_request(bundle, req)
        req.out_path.parent.mkdir(parents=True, exist_ok=True)
        length_s = duration_to_ltx_seconds(req.duration_s)
        actual_seed = req.seed if req.seed is not None else random.randint(1, 2**32 - 1)
        uploaded_name = self._comfy.upload_image(req.image_path)

        log.info(
            "ltx_i2v_start",
            image=req.image_path.name,
            uploaded_as=uploaded_name,
            length_s=length_s,
            out=req.out_path.name,
            prompt_preview=req.motion_prompt[:80],
        )
        wf = self._build_workflow(
            bundle,
            uploaded_name=uploaded_name,
            motion_prompt=req.motion_prompt,
            duration_s=req.duration_s,
            seed=actual_seed,
        )
        pid = self._comfy.queue_prompt(wf)
        hist = self._comfy.wait_for_completion(pid)
        self._save_clip_from_history(hist, req.out_path, length_s=length_s)
