#!/usr/bin/env bash
# Pod bootstrap — RunPod / Spheron / any Linux GPU pod.
#
# One-shot setup: ComfyUI + custom nodes + all model weights + Ollama + Qwen3 LLM
# + this repo + Python deps + smoke-test launch. Idempotent: re-running skips
# anything already installed or downloaded.
#
# Usage on a fresh pod (paste into the web terminal):
#   curl -fsSL https://raw.githubusercontent.com/<USER>/<REPO>/main/pod_bootstrap.sh | bash
# or after git clone:
#   bash pod_bootstrap.sh
#
# Env overrides (set BEFORE running):
#   REPO_URL=https://github.com/<USER>/<REPO>.git   # required if no local clone
#   GIT_BRANCH=main
#   OLLAMA_MODEL=qwen3.6:27b                          # model tag for Ollama pull
#   SKIP_MODELS=0                                   # 1 = skip huggingface downloads
#   SKIP_SMOKE=0                                    # 1 = setup only, don't launch render
#   NICHES=history,crime,military                   # subset for smoke test

set -euo pipefail

ROOT=/workspace
REPO_DIR=${REPO_DIR:-$ROOT/shorts}
COMFY_DIR=${COMFY_DIR:-$ROOT/ComfyUI}
LOG_DIR=$ROOT/logs
mkdir -p "$LOG_DIR"

log() { echo -e "\n\033[1;36m[bootstrap]\033[0m $*"; }
warn() { echo -e "\033[1;33m[bootstrap]\033[0m $*" >&2; }
die() { echo -e "\033[1;31m[bootstrap]\033[0m $*" >&2; exit 1; }

# ─── 1. System deps ──────────────────────────────────────────────────────────
log "Installing system packages…"
apt-get update -qq
apt-get install -y -qq git curl wget ffmpeg tmux htop nvtop nano rsync ca-certificates

# ─── 2. ComfyUI ──────────────────────────────────────────────────────────────
if [ ! -d "$COMFY_DIR" ]; then
  log "Cloning ComfyUI…"
  git clone --depth 1 https://github.com/comfyanonymous/ComfyUI.git "$COMFY_DIR"
fi

cd "$COMFY_DIR"
log "Installing ComfyUI Python deps…"
pip install -q -r requirements.txt

log "Installing custom nodes…"
mkdir -p custom_nodes && cd custom_nodes
[ -d ComfyUI-Manager ] || git clone --depth 1 https://github.com/ltdrdata/ComfyUI-Manager.git
# VideoHelperSuite: required for VHS_VideoCombine node in wan22_i2v_a14b.json
[ -d ComfyUI-VideoHelperSuite ] || git clone --depth 1 https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git
# WanVideoWrapper: optional fallback (workflow uses native Wan 2.2 nodes since Dec 2025)
[ -d ComfyUI-WanVideoWrapper ] || git clone --depth 1 https://github.com/kijai/ComfyUI-WanVideoWrapper.git
# Install per-node deps if requirements.txt present
for d in ComfyUI-Manager ComfyUI-VideoHelperSuite ComfyUI-WanVideoWrapper; do
  if [ -f "$d/requirements.txt" ]; then
    log "  installing deps for $d…"
    pip install -q -r "$d/requirements.txt" || warn "  deps for $d failed (non-fatal)"
  fi
done
cd "$COMFY_DIR"

# ─── 3. Models ───────────────────────────────────────────────────────────────
if [ "${SKIP_MODELS:-0}" != "1" ]; then
  log "Setting up HuggingFace CLI + hf_transfer…"
  pip install -q -U "huggingface_hub[cli]" hf_transfer
  export HF_HUB_ENABLE_HF_TRANSFER=1

  mkdir -p models/{diffusion_models,text_encoders,vae,upscale_models}
  cd models

  fetch() {
    local repo=$1 src=$2 dest_dir=$3
    local fname; fname=$(basename "$src")
    if [ -f "$dest_dir/$fname" ]; then
      log "  ✓ $fname (exists)"
      return
    fi
    log "  ↓ $fname  ($repo)"
    hf download "$repo" "$src" --local-dir . --local-dir-use-symlinks False >/dev/null
    mv "$src" "$dest_dir/"
  }

  log "Downloading Qwen Image 2512 weights…"
  fetch Comfy-Org/Qwen-Image_ComfyUI            split_files/diffusion_models/qwen_image_2512_bf16.safetensors  diffusion_models
  fetch Comfy-Org/Qwen-Image_ComfyUI            split_files/text_encoders/qwen_2.5_vl_7b.safetensors          text_encoders
  fetch Comfy-Org/Qwen-Image_ComfyUI            split_files/vae/qwen_image_vae.safetensors                    vae

  log "Downloading Wan 2.2 I2V 14B weights…"
  fetch Comfy-Org/Wan_2.2_ComfyUI_Repackaged    split_files/diffusion_models/wan2.2_i2v_high_noise_14B_bf16.safetensors  diffusion_models
  fetch Comfy-Org/Wan_2.2_ComfyUI_Repackaged    split_files/diffusion_models/wan2.2_i2v_low_noise_14B_bf16.safetensors   diffusion_models
  fetch Comfy-Org/Wan_2.1_ComfyUI_repackaged    split_files/text_encoders/umt5_xxl_fp16.safetensors           text_encoders
  fetch Comfy-Org/Wan_2.1_ComfyUI_repackaged    split_files/vae/wan_2.1_vae.safetensors                       vae

  log "Downloading 4x-UltraSharp upscaler…"
  if [ ! -f upscale_models/4x-UltraSharp.pth ]; then
    hf download Kim2091/UltraSharp 4x-UltraSharp.pth --local-dir upscale_models --local-dir-use-symlinks False >/dev/null
  fi

  rm -rf split_files

  log "Model footprint:"
  du -sh diffusion_models text_encoders vae upscale_models
fi

# ─── 4. Ollama (LLM backend) ─────────────────────────────────────────────────
if ! command -v ollama >/dev/null 2>&1; then
  log "Installing Ollama…"
  curl -fsSL https://ollama.com/install.sh | sh
fi

log "Starting Ollama service…"
pkill -f "ollama serve" || true
# Persist Ollama models on the network volume so pod restarts don't re-download.
export OLLAMA_MODELS="$ROOT/ollama_models"
mkdir -p "$OLLAMA_MODELS"
OLLAMA_KEEP_ALIVE=2m OLLAMA_MODELS="$OLLAMA_MODELS" nohup ollama serve >"$LOG_DIR/ollama.log" 2>&1 &
sleep 4

OLLAMA_MODEL=${OLLAMA_MODEL:-qwen3.6:27b}
if ! ollama list | grep -q "$OLLAMA_MODEL"; then
  log "Pulling LLM model: $OLLAMA_MODEL…"
  ollama pull "$OLLAMA_MODEL"
fi

# ─── 5. Shorts repo ──────────────────────────────────────────────────────────
if [ ! -d "$REPO_DIR" ]; then
  [ -n "${REPO_URL:-}" ] || die "REPO_DIR not found and REPO_URL not set"
  log "Cloning $REPO_URL into $REPO_DIR…"
  git clone --depth 1 -b "${GIT_BRANCH:-main}" "$REPO_URL" "$REPO_DIR"
fi
cd "$REPO_DIR"

log "Installing shorts pipeline Python deps…"
pip install -q -e . 2>/dev/null || pip install -q -r requirements.txt 2>/dev/null || true
pip install -q telethon pydantic-settings python-dotenv kokoro soundfile faster-whisper

# ─── 6. .env safety check ────────────────────────────────────────────────────
if [ ! -f .env ]; then
  warn ".env not found in $REPO_DIR — copy from local or create manually."
  warn "Required keys: SHORTS_TG_API_ID, SHORTS_TG_API_HASH, SHORTS_TG_TARGET_CHAT"
fi

# ─── 7. ComfyUI server ───────────────────────────────────────────────────────
log "Starting ComfyUI server on :8188…"
pkill -f "ComfyUI/main.py" || true
cd "$COMFY_DIR"
nohup python main.py --listen 0.0.0.0 --port 8188 >"$LOG_DIR/comfyui.log" 2>&1 &

log "Waiting for ComfyUI to come up…"
for i in {1..60}; do
  if curl -sf http://127.0.0.1:8188/system_stats >/dev/null 2>&1; then
    log "  ComfyUI ready."
    break
  fi
  sleep 2
  [ $i -eq 60 ] && warn "ComfyUI did not become ready in 120s — check $LOG_DIR/comfyui.log"
done

# ─── 8. Telegram session ─────────────────────────────────────────────────────
cd "$REPO_DIR"
if [ ! -f tg_session.session ]; then
  warn "tg_session.session NOT FOUND — first Telegram send will prompt for phone + SMS code."
  warn "Upload your existing session file via the RunPod web file manager to skip this."
fi

# ─── 9. Smoke test (16 niches × 1 full video) ────────────────────────────────
if [ "${SKIP_SMOKE:-0}" != "1" ]; then
  log "Launching smoke test (16 niches × 1 full video)…"
  cd "$REPO_DIR"
  EXTRA=()
  [ -n "${NICHES:-}" ] && EXTRA=(--niches "$NICHES")
  exec python smoke_test_16.py --full "${EXTRA[@]}" 2>&1 | tee "$LOG_DIR/smoke_test.log"
else
  log "Setup complete. Skipping smoke test (SKIP_SMOKE=1)."
  log "Run manually:  cd $REPO_DIR && python smoke_test_16.py --full"
fi
