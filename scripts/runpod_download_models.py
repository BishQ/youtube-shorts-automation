#!/usr/bin/env python3
"""Download all ComfyUI models for the local Flux 2 Dev + LTX 2.3 BF16 pipeline.

Designed to run on a RunPod GPU pod (or any Linux box with ComfyUI). Uses
``huggingface_hub`` for resumable parallel downloads.

Usage (RunPod terminal — paste after ComfyUI is installed):

    export HF_TOKEN=hf_xxxxxxxx          # required for gated FLUX.2-dev
    export COMFYUI_ROOT=/workspace/ComfyUI
    pip install -q -U "huggingface_hub[cli]"
    python runpod_download_models.py

One-liner bootstrap (from repo root on pod):

    bash scripts/runpod_bootstrap.sh

Sources (researched May 2026):
  - Flux UNET bf16: black-forest-labs/FLUX.2-dev (gated — accept license on HF first)
  - Flux text/VAE: Comfy-Org/flux2-dev split_files
  - LTX transformer + upscaler + distill LoRA: Lightricks/LTX-2.3
  - LTX Gemma bf16: Comfy-Org/ltx-2 split_files
  - LTX VAE + text connectors: unsloth/LTX-2.3-GGUF (safetensors aux files, not GGUF)

Total disk: ~180 GB. Recommended: RunPod network volume 200 GB+.
VRAM: H100 / A100 80 GB for full BF16 Flux + LTX.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    from huggingface_hub import hf_hub_download, login, snapshot_download
except ImportError:
    print("Install: pip install -U 'huggingface_hub[cli]'", file=sys.stderr)
    raise


@dataclass(frozen=True)
class ModelSpec:
    repo_id: str
    filename: str
    dest_subdir: str
    dest_name: str | None = None
    min_bytes: int = 1_000_000
    gated: bool = False
    note: str = ""


# Matches workflows/flux2_dev_lora_full.json + workflows/ltx23_i2v_full.json
MODELS: list[ModelSpec] = [
    # ── Flux 2 Dev (image) ───────────────────────────────────────────────────
    ModelSpec(
        repo_id="black-forest-labs/FLUX.2-dev",
        filename="flux2-dev.safetensors",
        dest_subdir="diffusion_models",
        min_bytes=50_000_000_000,
        gated=True,
        note="Accept FLUX license at huggingface.co/black-forest-labs/FLUX.2-dev",
    ),
    ModelSpec(
        repo_id="Comfy-Org/flux2-dev",
        filename="split_files/text_encoders/mistral_3_small_flux2_bf16.safetensors",
        dest_subdir="text_encoders",
        dest_name="mistral_3_small_flux2_bf16.safetensors",
        min_bytes=30_000_000_000,
    ),
    ModelSpec(
        repo_id="Comfy-Org/flux2-dev",
        filename="split_files/vae/flux2-vae.safetensors",
        dest_subdir="vae",
        dest_name="flux2-vae.safetensors",
        min_bytes=100_000_000,
    ),
    # ── LTX 2.3 Dev (I2V) ────────────────────────────────────────────────────
    ModelSpec(
        repo_id="Lightricks/LTX-2.3",
        filename="ltx-2.3-22b-dev.safetensors",
        dest_subdir="diffusion_models",
        min_bytes=40_000_000_000,
    ),
    ModelSpec(
        repo_id="Comfy-Org/ltx-2",
        filename="split_files/text_encoders/gemma_3_12B_it.safetensors",
        dest_subdir="text_encoders",
        dest_name="comfy_gemma_3_12B_it.safetensors",
        min_bytes=20_000_000_000,
        note="Workflow expects comfy_gemma_3_12B_it.safetensors",
    ),
    ModelSpec(
        repo_id="unsloth/LTX-2.3-GGUF",
        filename="text_encoders/ltx-2.3-22b-dev_embeddings_connectors.safetensors",
        dest_subdir="text_encoders",
        min_bytes=1_000_000_000,
        note="Official aux safetensors bundle (repo name is historical)",
    ),
    ModelSpec(
        repo_id="unsloth/LTX-2.3-GGUF",
        filename="vae/ltx-2.3-22b-dev_video_vae.safetensors",
        dest_subdir="vae",
        min_bytes=500_000_000,
    ),
    ModelSpec(
        repo_id="unsloth/LTX-2.3-GGUF",
        filename="vae/ltx-2.3-22b-dev_audio_vae.safetensors",
        dest_subdir="vae",
        min_bytes=100_000_000,
    ),
    ModelSpec(
        repo_id="Lightricks/LTX-2.3",
        filename="ltx-2.3-spatial-upscaler-x2-1.0.safetensors",
        dest_subdir="latent_upscale_models",
        min_bytes=500_000_000,
    ),
    ModelSpec(
        repo_id="Lightricks/LTX-2.3",
        filename="ltx-2.3-22b-distilled-lora-384.safetensors",
        dest_subdir="loras",
        min_bytes=5_000_000_000,
        note="Dev model + distill LoRA (workflow node 134)",
    ),
]

# workflows/flux2_dev_identity_full.json (PuLID + InstantID kps + ControlNet inpaint)
IDENTITY_MODELS: list[ModelSpec] = [
    ModelSpec(
        repo_id="Fayens/Pulid-Flux2",
        filename="pulid_flux2_klein_v2.safetensors",
        dest_subdir="pulid",
        min_bytes=800_000_000,
        note="PuLID Flux2 identity weights (no LoRA training)",
    ),
    ModelSpec(
        repo_id="alibaba-pai/FLUX.2-dev-Fun-Controlnet-Union",
        filename="FLUX.2-dev-Fun-Controlnet-Union.safetensors",
        dest_subdir="controlnet",
        min_bytes=7_000_000_000,
        note="Flux2 Fun ControlNet Union (pose/kps + inpaint refine)",
    ),
]

OPTIONAL_UPSCALE = ModelSpec(
    repo_id="Kim2091/UltraSharp",
    filename="4x-UltraSharp.pth",
    dest_subdir="upscale_models",
    min_bytes=50_000_000,
    note="Only if SHORTS_COMFY_UPSCALE_WORKFLOW_NAME=upscale_4x_ultrasharp",
)


def _fmt_gb(n: int) -> str:
    return f"{n / (1024**3):.2f} GB"


def _comfy_models_root(comfy_root: Path) -> Path:
    return comfy_root / "models"


def _dest_path(models_root: Path, spec: ModelSpec) -> Path:
    name = spec.dest_name or Path(spec.filename).name
    return models_root / spec.dest_subdir / name


def _already_ok(path: Path, min_bytes: int) -> bool:
    return path.is_file() and path.stat().st_size >= min_bytes


def _download_one(
    spec: ModelSpec,
    models_root: Path,
    *,
    token: str | None,
    force: bool,
) -> None:
    dest = _dest_path(models_root, spec)
    dest.parent.mkdir(parents=True, exist_ok=True)

    if not force and _already_ok(dest, spec.min_bytes):
        print(f"  SKIP (exists) {dest.name}  [{_fmt_gb(dest.stat().st_size)}]")
        return

    print(f"  GET  {spec.repo_id} / {spec.filename}")
    if spec.note:
        print(f"       ({spec.note})")

    cached = hf_hub_download(
        repo_id=spec.repo_id,
        filename=spec.filename,
        token=token,
        resume_download=True,
    )
    cached_path = Path(cached)
    if cached_path.resolve() != dest.resolve():
        if dest.exists():
            dest.unlink()
        shutil.copy2(cached_path, dest)

    size = dest.stat().st_size
    if size < spec.min_bytes:
        raise RuntimeError(
            f"Download looks truncated: {dest} ({size} bytes, expected >= {spec.min_bytes})"
        )
    print(f"  OK   {dest.name}  [{_fmt_gb(size)}]")


def _install_ltx_custom_node(comfy_root: Path) -> None:
    _clone_custom_node(
        comfy_root,
        folder="ComfyUI-LTXVideo",
        repo_url="https://github.com/Lightricks/ComfyUI-LTXVideo.git",
        pip_requirements=True,
    )


def _clone_custom_node(
    comfy_root: Path,
    *,
    folder: str,
    repo_url: str,
    pip_requirements: bool = False,
    pip_packages: list[str] | None = None,
) -> None:
    target = comfy_root / "custom_nodes" / folder
    if (target / ".git").is_dir() or (target / "pyproject.toml").is_file():
        print(f"  SKIP {folder} (already at {target})")
        return
    comfy_root.joinpath("custom_nodes").mkdir(parents=True, exist_ok=True)
    print(f"  CLONE {folder} …")
    subprocess.run(
        ["git", "clone", "--depth", "1", repo_url, str(target)],
        check=True,
    )
    if pip_requirements:
        req = target / "requirements.txt"
        if req.is_file():
            print(f"  PIP  {folder} requirements …")
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "-q", "-r", str(req)],
                check=True,
            )
    if pip_packages:
        print(f"  PIP  {folder} extras …")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-q", *pip_packages],
            check=True,
        )


def _install_identity_custom_nodes(comfy_root: Path) -> None:
    """PuLID-Flux2 + InstantID keypoints + Flux2 Fun ControlNet."""
    _clone_custom_node(
        comfy_root,
        folder="ComfyUI-PuLID-Flux2",
        repo_url="https://github.com/iFayens/ComfyUI-PuLID-Flux2.git",
        pip_packages=[
            "insightface",
            "onnxruntime-gpu",
            "open-clip-torch",
            "safetensors",
            "ml_dtypes==0.3.2",
        ],
    )
    _clone_custom_node(
        comfy_root,
        folder="ComfyUI_InstantID",
        repo_url="https://github.com/cubiq/ComfyUI_InstantID.git",
        pip_packages=["insightface", "onnxruntime-gpu"],
    )
    _clone_custom_node(
        comfy_root,
        folder="comfyui-flux2fun-controlnet",
        repo_url="https://github.com/bryanmcguire/comfyui-flux2fun-controlnet.git",
    )


def _install_antelopev2(models_root: Path, *, force: bool) -> None:
    """InsightFace antelopev2 for PuLID + InstantID (ONNX in flat folder)."""
    target = models_root / "insightface" / "models" / "antelopev2"
    marker = target / "1k3d68.onnx"
    if not force and marker.is_file():
        print(f"  SKIP antelopev2 (exists at {target})")
        return
    if target.exists() and force:
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    print("  GET  kidyu/antelopev2-for-InstantID-ComfyUI → insightface/models/antelopev2/")
    snapshot_download(
        repo_id="kidyu/antelopev2-for-InstantID-ComfyUI",
        local_dir=str(target),
        local_dir_use_symlinks=False,
    )
    if not marker.is_file():
        nested = target / "antelopev2"
        if nested.is_dir():
            for item in nested.iterdir():
                dest = target / item.name
                if dest.exists():
                    if dest.is_dir():
                        shutil.rmtree(dest)
                    else:
                        dest.unlink()
                shutil.move(str(item), str(dest))
            nested.rmdir()
    if not marker.is_file():
        raise RuntimeError(f"antelopev2 install incomplete: missing {marker}")


def _resolve_token(cli_token: str | None) -> str | None:
    if cli_token:
        return cli_token.strip()
    for key in ("HF_TOKEN", "HUGGINGFACE_HUB_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
        val = (os.environ.get(key) or "").strip()
        if val:
            return val
    return None


def main() -> int:
    p = argparse.ArgumentParser(description="Download Flux 2 + LTX 2.3 BF16 models for ComfyUI")
    p.add_argument(
        "--comfy-root",
        default=os.environ.get("COMFYUI_ROOT", "/workspace/ComfyUI"),
        help="ComfyUI install root (default: $COMFYUI_ROOT or /workspace/ComfyUI)",
    )
    p.add_argument("--hf-token", default=None, help="HuggingFace token (or set HF_TOKEN)")
    p.add_argument("--skip-flux", action="store_true", help="Skip Flux 2 Dev downloads")
    p.add_argument("--skip-ltx", action="store_true", help="Skip LTX 2.3 downloads")
    p.add_argument(
        "--skip-identity",
        action="store_true",
        help="Skip PuLID/ControlNet/antelopev2 + identity custom nodes",
    )
    p.add_argument("--with-upscale", action="store_true", help="Also fetch 4x-UltraSharp.pth")
    p.add_argument(
        "--skip-custom-nodes",
        action="store_true",
        help="Do not clone ComfyUI-LTXVideo or flux_identity custom nodes",
    )
    p.add_argument("--force", action="store_true", help="Re-download even if file exists")
    args = p.parse_args()

    comfy_root = Path(args.comfy_root).expanduser().resolve()
    models_root = _comfy_models_root(comfy_root)
    token = _resolve_token(args.hf_token)

    print("=" * 72)
    print("RunPod model bootstrap — Flux 2 Dev BF16 + identity stack + LTX 2.3 Dev BF16")
    print(f"ComfyUI root : {comfy_root}")
    print(f"Models dir   : {models_root}")
    print(f"HF token     : {'set' if token else 'NOT SET (gated Flux will fail)'}")
    print("=" * 72)

    if token:
        try:
            login(token=token, add_to_git_credential=False)
        except Exception as exc:
            print(f"WARN: huggingface login: {exc}")

    specs = list(MODELS)
    if args.skip_flux:
        specs = [s for s in specs if "flux" not in s.filename.lower() and "FLUX" not in s.repo_id]
    if args.skip_ltx:
        specs = [
            s
            for s in specs
            if "ltx" not in s.filename.lower() and "LTX" not in s.repo_id and "gemma" not in s.filename.lower()
        ]
    if not args.skip_identity and not args.skip_flux:
        specs.extend(IDENTITY_MODELS)
    if args.with_upscale:
        specs.append(OPTIONAL_UPSCALE)

    flux_gated = [s for s in specs if s.gated]
    if flux_gated and not token:
        print(
            "\nERROR: HF_TOKEN required for gated model black-forest-labs/FLUX.2-dev\n"
            "  1. Create token: https://huggingface.co/settings/tokens\n"
            "  2. Accept license: https://huggingface.co/black-forest-labs/FLUX.2-dev\n"
            "  3. export HF_TOKEN=hf_...\n",
            file=sys.stderr,
        )
        return 2

    print("\n── Models ──")
    errors: list[str] = []
    for i, spec in enumerate(specs, 1):
        print(f"\n[{i}/{len(specs)}] {spec.dest_subdir}/{spec.dest_name or Path(spec.filename).name}")
        try:
            _download_one(spec, models_root, token=token, force=args.force)
        except Exception as exc:
            print(f"  FAIL {exc}", file=sys.stderr)
            errors.append(str(exc))

    if not args.skip_custom_nodes and not args.skip_ltx:
        print("\n── Custom nodes (LTX) ──")
        try:
            _install_ltx_custom_node(comfy_root)
        except Exception as exc:
            print(f"  FAIL ComfyUI-LTXVideo: {exc}", file=sys.stderr)
            errors.append(str(exc))

    if not args.skip_custom_nodes and not args.skip_identity and not args.skip_flux:
        print("\n── Custom nodes (flux_identity) ──")
        try:
            _install_identity_custom_nodes(comfy_root)
        except Exception as exc:
            print(f"  FAIL identity nodes: {exc}", file=sys.stderr)
            errors.append(str(exc))

    if not args.skip_identity and not args.skip_flux:
        print("\n── InsightFace antelopev2 ──")
        try:
            _install_antelopev2(models_root, force=args.force)
        except Exception as exc:
            print(f"  FAIL antelopev2: {exc}", file=sys.stderr)
            errors.append(str(exc))

    print("\n" + "=" * 72)
    if errors:
        print(f"DONE with {len(errors)} error(s). Fix and re-run (downloads resume).")
        return 1

    print("All models ready. Restart ComfyUI, then set on your laptop:")
    print("  SHORTS_IMAGE_BACKEND=flux_identity")
    print("  SHORTS_COMFY_BASE_URL=https://<pod-id>-8188.proxy.runpod.net")
    print("\nNo person LoRA training — identity uses 4 ref photos + PuLID + ControlNet inpaint.")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
