"""Standalone local ComfyUI smoke test — Qwen-Image-2512 + Wan 2.2 I2V 14B.

Run this BEFORE batch jobs to verify your local GPU stack:
  1. ComfyUI is reachable at SHORTS_COMFY_BASE_URL (default http://127.0.0.1:8188)
  2. Qwen-Image-2512 bf16 workflow runs end-to-end
  3. Wan 2.2 I2V-A14B workflow runs using the generated image
  4. Output bytes decode cleanly into PNG + MP4 on disk

Usage (PowerShell):
    # Start ComfyUI first, then:
    python local_comfy_smoke_test.py

Optional:
    --image-only    skip the I2V stage
    --prompt "..."  override the test prompt
    --out DIR       output directory (default ./local_smoke_out)
    --comfy-url URL override SHORTS_COMFY_BASE_URL

Expected VRAM (bf16, both models loaded sequentially in ComfyUI):
    Qwen-Image-2512: ~40 GB
    Wan 2.2 I2V 14B:  ~40 GB (high + low noise models)
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

SRC = Path(__file__).resolve().parent / "src"
if SRC.exists():
    sys.path.insert(0, str(SRC))

from shorts_pipeline.config.settings import Settings
from shorts_pipeline.image_worker.comfy import ComfyError, load_workflow_bundle
from shorts_pipeline.runpod_adapter import make_i2v_client, make_image_client
from shorts_pipeline.video_worker.motion_templates import default_motion_prompt
from shorts_pipeline.video_worker.wan_i2v import WanI2VError, load_i2v_bundle


DEFAULT_PROMPT = (
    "A grizzled Roman general in tarnished bronze armor stands on a "
    "blood-stained battlefield at golden hour, medium shot, dramatic side "
    "light, dust motes drifting through warm beams, hyper-realistic cinematic "
    "photography, shallow depth of field"
)


def _fmt_dur(start: float) -> str:
    return f"{time.time() - start:.1f}s"


def main() -> int:
    p = argparse.ArgumentParser(description="Local ComfyUI smoke test (Qwen-2512 + Wan I2V)")
    p.add_argument("--image-only", action="store_true", help="skip the I2V stage")
    p.add_argument("--prompt", default=DEFAULT_PROMPT, help="positive prompt for the image")
    p.add_argument(
        "--motion",
        default=None,
        help="motion prompt for I2V (default: history niche template)",
    )
    p.add_argument("--duration", type=float, default=4.0, help="clip duration in seconds (2.0-7.0)")
    p.add_argument("--out", default="./local_smoke_out", help="output directory")
    p.add_argument(
        "--comfy-url",
        default=None,
        help="ComfyUI base URL (overrides SHORTS_COMFY_BASE_URL)",
    )
    p.add_argument(
        "--image-workflow",
        default="qwen_image_2512_local",
        help="workflow JSON name under workflows/ for image gen",
    )
    p.add_argument(
        "--i2v-workflow",
        default="wan22_i2v_a14b",
        help="workflow JSON name under workflows/ for I2V",
    )
    args = p.parse_args()

    settings = Settings(comfy_mode="local")
    if args.comfy_url:
        settings = settings.model_copy(update={"comfy_base_url": args.comfy_url.rstrip("/")})

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── Stage 1: image ────────────────────────────────────────────────────────
    img_bundle_path = (settings.workflows_dir / f"{args.image_workflow}.json").resolve()
    if not img_bundle_path.exists():
        print(f"ERROR: image workflow not found: {img_bundle_path}", file=sys.stderr)
        return 2
    img_bundle = load_workflow_bundle(img_bundle_path)
    img_client = make_image_client(settings)

    img_out = out_dir / "smoke_image.png"
    print(f"[1/2] Generating image via local ComfyUI @ {settings.comfy_base_url}...")
    print(f"      workflow:  {img_bundle_path.name}")
    print(f"      prompt:    {args.prompt[:100]}{'...' if len(args.prompt) > 100 else ''}")
    t0 = time.time()
    try:
        img_client.generate_one(img_bundle, args.prompt, img_out)
    except ComfyError as e:
        print(f"      FAILED in {_fmt_dur(t0)}: {e}", file=sys.stderr)
        if e.detail:
            print(f"      detail: {str(e.detail)[:500]}", file=sys.stderr)
        return 1
    print(f"      OK in {_fmt_dur(t0)} -> {img_out} ({img_out.stat().st_size // 1024} KB)")

    if args.image_only:
        print("--image-only: skipping I2V stage")
        return 0

    # ── Stage 2: I2V ──────────────────────────────────────────────────────────
    i2v_bundle_path = (settings.workflows_dir / f"{args.i2v_workflow}.json").resolve()
    if not i2v_bundle_path.exists():
        print(f"ERROR: I2V workflow not found: {i2v_bundle_path}", file=sys.stderr)
        return 2
    i2v_bundle = load_i2v_bundle(i2v_bundle_path)
    i2v_client = make_i2v_client(settings)

    motion = args.motion or default_motion_prompt(niche="history")
    clip_out = out_dir / "smoke_clip.mp4"
    print("[2/2] Generating I2V clip via local ComfyUI...")
    print(f"      workflow:  {i2v_bundle_path.name}")
    print(f"      duration:  {args.duration}s")
    print(f"      motion:    {motion[:100]}{'...' if len(motion) > 100 else ''}")
    t1 = time.time()
    try:
        i2v_client.generate_clip(
            i2v_bundle,
            image_path=img_out,
            motion_prompt=motion,
            duration_s=args.duration,
            out_path=clip_out,
        )
    except (ComfyError, WanI2VError) as e:
        print(f"      FAILED in {_fmt_dur(t1)}: {e}", file=sys.stderr)
        detail = getattr(e, "detail", None)
        if detail:
            print(f"      detail: {str(detail)[:500]}", file=sys.stderr)
        return 1
    print(f"      OK in {_fmt_dur(t1)} -> {clip_out} ({clip_out.stat().st_size // 1024} KB)")

    total_s = time.time() - t0
    print(f"\nSmoke test PASSED — wall {total_s:.1f}s")
    print(f"Outputs in: {out_dir.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
