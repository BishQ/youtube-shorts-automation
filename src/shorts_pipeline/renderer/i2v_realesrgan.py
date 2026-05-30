"""Real-ESRGAN upscaling for I2V clause MP4s before FFmpeg render."""

from __future__ import annotations

import copy
import subprocess
import tempfile
from pathlib import Path

from shorts_pipeline.config.settings import Settings
from shorts_pipeline.image_worker.comfy import (
    ComfyClient,
    UpscaleBundle,
    _nested_set,
    _pick_last_png,
)
from shorts_pipeline.logging_setup import get_logger

log = get_logger(__name__)


class I2VRealESRGANError(RuntimeError):
    """Raised when Real-ESRGAN I2V upscale fails."""


def _run(cmd: list[str], *, what: str) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "")[-1200:]
        raise I2VRealESRGANError(f"{what} failed (exit {result.returncode}): {tail}")


def _probe_fps(ffprobe_path: str, media: Path) -> float:
    cmd = [
        ffprobe_path,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=r_frame_rate",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(media),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return 24.0
    raw = (result.stdout or "").strip()
    if "/" in raw:
        num, den = raw.split("/", 1)
        try:
            return float(num) / float(den)
        except (ValueError, ZeroDivisionError):
            pass
    try:
        return float(raw)
    except ValueError:
        return 24.0


def upscale_clip_realesrgan_comfy(
    comfy: ComfyClient,
    bundle: UpscaleBundle,
    *,
    input_mp4: Path,
    output_mp4: Path,
    settings: Settings,
) -> None:
    """Extract frames → Real-ESRGAN x2/x4 on RunPod Comfy → re-mux MP4."""
    if not input_mp4.is_file():
        raise I2VRealESRGANError(f"missing input clip: {input_mp4}")

    output_mp4.parent.mkdir(parents=True, exist_ok=True)
    fps = _probe_fps(settings.ffprobe_path, input_mp4)
    cap_fps = float(settings.render_i2v_realesrgan_fps)
    extract_fps = min(fps, cap_fps) if cap_fps > 0 else fps

    with tempfile.TemporaryDirectory(prefix="realesrgan_") as tmp:
        tmp_path = Path(tmp)
        frames_in = tmp_path / "in"
        frames_up = tmp_path / "up"
        frames_in.mkdir()
        frames_up.mkdir()

        _run(
            [
                settings.ffmpeg_path,
                "-y",
                "-i",
                str(input_mp4),
                "-vf",
                f"fps={extract_fps:.6f}",
                str(frames_in / "%06d.png"),
            ],
            what="ffmpeg extract frames",
        )
        src_frames = sorted(frames_in.glob("*.png"))
        if not src_frames:
            raise I2VRealESRGANError(f"no frames extracted from {input_mp4.name}")

        pending: list[tuple[Path, Path]] = []
        workflows: list[dict] = []

        for src in src_frames:
            out_png = frames_up / src.name
            if out_png.is_file() and out_png.stat().st_size > 1000:
                continue
            uploaded = comfy.upload_image(src)
            wf = copy.deepcopy(bundle.prompt)
            _nested_set(wf, bundle.image_key_path, uploaded)
            pending.append((src, out_png))
            workflows.append(wf)

        if workflows:
            prompt_ids: list[str] = []
            for wf in workflows:
                prompt_ids.append(comfy.queue_prompt(wf))
            log.info(
                "realesrgan_comfy_batch_queued",
                clip=input_mp4.name,
                frames=len(prompt_ids),
            )
            hist_map = comfy.wait_for_prompts(prompt_ids)

            for pid, (_src, out_png) in zip(prompt_ids, pending):
                hist = hist_map[pid]
                outputs = hist.get("outputs") or {}
                png_name, subfolder = _pick_last_png(outputs)
                data = comfy.fetch_output_png(png_name, subfolder=subfolder)
                out_png.write_bytes(data)

        for src in src_frames:
            dest = frames_up / src.name
            if not dest.is_file():
                dest.write_bytes(src.read_bytes())

        up_frames = sorted(frames_up.glob("*.png"))
        if len(up_frames) != len(src_frames):
            raise I2VRealESRGANError(
                f"frame count mismatch after upscale: {len(up_frames)} != {len(src_frames)}"
            )

        _run(
            [
                settings.ffmpeg_path,
                "-y",
                "-framerate",
                f"{extract_fps:.6f}",
                "-i",
                str(frames_up / "%06d.png"),
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "16",
                "-pix_fmt",
                "yuv420p",
                str(output_mp4),
            ],
            what="ffmpeg reassemble upscaled clip",
        )

    size_kb = output_mp4.stat().st_size // 1024
    log.info(
        "realesrgan_comfy_clip_done",
        clip_in=input_mp4.name,
        clip_out=output_mp4.name,
        size_kb=size_kb,
        extract_fps=round(extract_fps, 3),
    )


def upscale_clip_realesrgan_ncnn(
    *,
    input_mp4: Path,
    output_mp4: Path,
    settings: Settings,
) -> None:
    """Upscale each frame with realesrgan-ncnn-vulkan (local GPU/CPU binary)."""
    binary_raw = (settings.realesrgan_ncnn_path or "").strip()
    if not binary_raw:
        raise I2VRealESRGANError("SHORTS_REALESRGAN_NCNN_PATH is not set")
    binary = Path(binary_raw)
    if not binary.is_file():
        raise I2VRealESRGANError(f"realesrgan-ncnn binary not found: {binary}")

    scale = int(settings.render_i2v_realesrgan_scale)
    model = (settings.realesrgan_ncnn_model or "realesrgan-x4plus").strip()
    output_mp4.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="realesrgan_ncnn_") as tmp:
        tmp_path = Path(tmp)
        frames_in = tmp_path / "in"
        frames_out = tmp_path / "out"
        frames_in.mkdir()
        frames_out.mkdir()

        fps = _probe_fps(settings.ffprobe_path, input_mp4)
        _run(
            [
                settings.ffmpeg_path,
                "-y",
                "-i",
                str(input_mp4),
                "-vf",
                f"fps={fps:.6f}",
                str(frames_in / "%06d.png"),
            ],
            what="ffmpeg extract frames",
        )
        for src in sorted(frames_in.glob("*.png")):
            dst = frames_out / src.name
            _run(
                [
                    str(binary),
                    "-i",
                    str(src),
                    "-o",
                    str(dst),
                    "-n",
                    model,
                    "-s",
                    str(scale),
                ],
                what=f"realesrgan-ncnn {src.name}",
            )

        _run(
            [
                settings.ffmpeg_path,
                "-y",
                "-framerate",
                f"{fps:.6f}",
                "-i",
                str(frames_out / "%06d.png"),
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "16",
                "-pix_fmt",
                "yuv420p",
                str(output_mp4),
            ],
            what="ffmpeg reassemble ncnn clip",
        )

    log.info("realesrgan_ncnn_clip_done", clip_in=input_mp4.name, clip_out=output_mp4.name, scale=scale)


def cached_upscaled_clip_path(source: Path, *, suffix: str = "realesrgan") -> Path:
    return source.parent / suffix / source.name


def upscale_i2v_clip_if_needed(
    source: Path,
    *,
    settings: Settings,
    comfy: ComfyClient | None = None,
    up_bundle: UpscaleBundle | None = None,
) -> Path:
    """Return Real-ESRGAN upscaled clip path, or source when mode is ffmpeg."""
    mode = (settings.render_i2v_upscale or "ffmpeg").lower().strip()
    if mode == "ffmpeg" or not source.is_file():
        return source

    out = cached_upscaled_clip_path(source)
    if out.is_file() and out.stat().st_mtime >= source.stat().st_mtime:
        return out

    if mode == "realesrgan_comfy":
        if comfy is None or up_bundle is None:
            raise I2VRealESRGANError("realesrgan_comfy requires Comfy client and upscale bundle")
        upscale_clip_realesrgan_comfy(
            comfy,
            up_bundle,
            input_mp4=source,
            output_mp4=out,
            settings=settings,
        )
        return out

    if mode == "realesrgan_ncnn":
        upscale_clip_realesrgan_ncnn(input_mp4=source, output_mp4=out, settings=settings)
        return out

    raise I2VRealESRGANError(f"unknown render_i2v_upscale mode: {mode!r}")


def prepare_i2v_paths_for_render(
    video_paths: list[Path | None],
    *,
    settings: Settings,
    comfy: ComfyClient | None = None,
    up_bundle: UpscaleBundle | None = None,
) -> list[Path | None]:
    mode = (settings.render_i2v_upscale or "ffmpeg").lower().strip()
    if mode == "ffmpeg":
        return video_paths
    out: list[Path | None] = []
    for p in video_paths:
        if p is None or not p.is_file():
            out.append(p)
            continue
        out.append(
            upscale_i2v_clip_if_needed(
                p,
                settings=settings,
                comfy=comfy,
                up_bundle=up_bundle,
            )
        )
    return out
