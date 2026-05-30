#!/usr/bin/env bash
# RunPod one-shot setup: deps + LTXVideo + flux_identity nodes + all BF16 models.
#
# Includes flux_identity stack (no LoRA train):
#   PuLID-Flux2, InstantID keypoints, Flux2 Fun ControlNet inpaint, antelopev2
#
# Paste on a fresh RunPod pod (ComfyUI template or manual install):
#
#   export HF_TOKEN=hf_xxxxxxxx
#   export COMFYUI_ROOT=/workspace/ComfyUI
#   bash runpod_bootstrap.sh
#
# Or from this repo on the pod:
#   cd /workspace/shorts-pipeline && bash scripts/runpod_bootstrap.sh
#
set -euo pipefail

COMFYUI_ROOT="${COMFYUI_ROOT:-/workspace/ComfyUI}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo ">>> Installing huggingface_hub …"
python3 -m pip install -q -U "huggingface_hub[cli]"

if [[ -z "${HF_TOKEN:-}" && -z "${HUGGINGFACE_HUB_TOKEN:-}" ]]; then
  echo ""
  echo "WARNING: HF_TOKEN not set."
  echo "  Flux 2 Dev (black-forest-labs/FLUX.2-dev) is GATED."
  echo "  1. Accept: https://huggingface.co/black-forest-labs/FLUX.2-dev"
  echo "  2. Token:  https://huggingface.co/settings/tokens"
  echo "  3. export HF_TOKEN=hf_..."
  echo ""
fi

echo ">>> Downloading models into ${COMFYUI_ROOT}/models …"
python3 "${SCRIPT_DIR}/runpod_download_models.py" \
  --comfy-root "${COMFYUI_ROOT}" \
  ${HF_TOKEN:+--hf-token "$HF_TOKEN"}

echo ">>> Done. Start ComfyUI and point SHORTS_COMFY_BASE_URL to this pod."
