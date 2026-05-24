#!/usr/bin/env bash
# Start vLLM OpenAI API server (Gemma 4 31B text-only on RunPod).
#
# Usage:
#   bash scripts/start_vllm.sh
#
# Env overrides:
#   VLLM_MODEL=/workspace/models/gemma4-31b
#   VLLM_SERVED_NAME=gemma4-31b
#   VLLM_PORT=8000
#   VLLM_TENSOR_PARALLEL_SIZE=2      # auto = GPU count (31B needs 80GB or TP>=2)
#   VLLM_MAX_MODEL_LEN=8192
#   VLLM_MAX_NUM_BATCHED_TOKENS=8192
#   VLLM_GPU_MEMORY_UTILIZATION=0.92
#   VLLM_KV_CACHE_DTYPE=fp8          # helps on 40–48GB GPUs
#   VLLM_LIMIT_MM='{"image":0,"audio":0,"video":0}'  # text-only (Shorts planner)

set -euo pipefail

ROOT=${ROOT:-/workspace}
LOG_DIR=${LOG_DIR:-$ROOT/logs}
mkdir -p "$LOG_DIR"

VLLM_MODEL=${VLLM_MODEL:-/workspace/models/gemma4-31b}
VLLM_SERVED_NAME=${VLLM_SERVED_NAME:-gemma4-31b}
VLLM_PORT=${VLLM_PORT:-8000}
VLLM_HOST=${VLLM_HOST:-0.0.0.0}
VLLM_MAX_NUM_BATCHED_TOKENS=${VLLM_MAX_NUM_BATCHED_TOKENS:-8192}
VLLM_MAX_MODEL_LEN=${VLLM_MAX_MODEL_LEN:-8192}
VLLM_GPU_MEMORY_UTILIZATION=${VLLM_GPU_MEMORY_UTILIZATION:-0.92}
VLLM_KV_CACHE_DTYPE=${VLLM_KV_CACHE_DTYPE:-fp8}
VLLM_LIMIT_MM=${VLLM_LIMIT_MM:-'{"image":0,"audio":0,"video":0}'}
VLLM_ENFORCE_EAGER=${VLLM_ENFORCE_EAGER:-1}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

log() { echo -e "\n\033[1;36m[vllm]\033[0m $*"; }
warn() { echo -e "\033[1;33m[vllm]\033[0m $*" >&2; }
die() { echo -e "\033[1;31m[vllm]\033[0m $*" >&2; exit 1; }

dump_vllm_errors() {
  local logfile=$1
  warn "=== GPU status ==="
  nvidia-smi 2>/dev/null || warn "nvidia-smi unavailable"
  warn "=== vLLM log (errors / OOM / CUDA) ==="
  grep -iE 'error|oom|cuda|failed|traceback|runtimeerror|valueerror|notimplemented' \
    "$logfile" 2>/dev/null | tail -80 || tail -80 "$logfile" 2>/dev/null || true
}

gpu_count() {
  if command -v nvidia-smi >/dev/null 2>&1; then
    nvidia-smi -L 2>/dev/null | wc -l
  else
    echo 1
  fi
}

pick_tensor_parallel() {
  if [ -n "${VLLM_TENSOR_PARALLEL_SIZE:-}" ]; then
    echo "$VLLM_TENSOR_PARALLEL_SIZE"
    return
  fi
  local gpus
  gpus=$(gpu_count)
  if [ "$gpus" -ge 2 ]; then
    echo 2
  else
    echo 1
  fi
}

if curl -sf "http://127.0.0.1:${VLLM_PORT}/v1/models" >/dev/null 2>&1; then
  log "Already running on port ${VLLM_PORT}."
  curl -sf "http://127.0.0.1:${VLLM_PORT}/v1/models"
  echo
  exit 0
fi

if [ ! -e "$VLLM_MODEL" ]; then
  die "Model not found: ${VLLM_MODEL} — set VLLM_MODEL to your local weights path"
fi

VLLM_TENSOR_PARALLEL_SIZE=$(pick_tensor_parallel)
GPUS=$(gpu_count)

log "GPUs detected: ${GPUS}  tensor-parallel-size=${VLLM_TENSOR_PARALLEL_SIZE}"
if [ "$GPUS" -lt 2 ] && echo "$VLLM_MODEL" | grep -qi '31b'; then
  warn "Gemma 4 31B BF16 needs ~80GB VRAM on 1 GPU (official vLLM guide)."
  warn "Single GPU: use VLLM_KV_CACHE_DTYPE=fp8 and VLLM_MAX_MODEL_LEN=8192, or pick a 2-GPU pod (TP=2)."
fi

log "Installing/upgrading vLLM…"
pip install -q -U vllm

if [ -f "$REPO_DIR/scripts/sync_env_llm.sh" ]; then
  VLLM_PORT="$VLLM_PORT" VLLM_SERVED_NAME="$VLLM_SERVED_NAME" \
    bash "$REPO_DIR/scripts/sync_env_llm.sh" "$REPO_DIR/.env"
fi

log "Starting Gemma 4 (text-only)…"
log "  model=${VLLM_MODEL}"
log "  served=${VLLM_SERVED_NAME}  port=${VLLM_PORT}  tp=${VLLM_TENSOR_PARALLEL_SIZE}"
log "  max_model_len=${VLLM_MAX_MODEL_LEN}  max_batched=${VLLM_MAX_NUM_BATCHED_TOKENS}"
log "  kv_cache=${VLLM_KV_CACHE_DTYPE}  limit_mm=${VLLM_LIMIT_MM}"

pkill -f "vllm.entrypoints.openai.api_server" || true
sleep 2

export HF_HOME="${HF_HOME:-$ROOT/hf_cache}"
mkdir -p "$HF_HOME"

VLLM_ARGS=(
  --model "$VLLM_MODEL"
  --served-model-name "$VLLM_SERVED_NAME"
  --port "$VLLM_PORT"
  --host "$VLLM_HOST"
  --tensor-parallel-size "$VLLM_TENSOR_PARALLEL_SIZE"
  --max-model-len "$VLLM_MAX_MODEL_LEN"
  --max-num-batched-tokens "$VLLM_MAX_NUM_BATCHED_TOKENS"
  --gpu-memory-utilization "$VLLM_GPU_MEMORY_UTILIZATION"
  --limit-mm-per-prompt "$VLLM_LIMIT_MM"
  --trust-remote-code
)

if [ "$VLLM_KV_CACHE_DTYPE" != "auto" ]; then
  VLLM_ARGS+=(--kv-cache-dtype "$VLLM_KV_CACHE_DTYPE")
fi
if [ "$VLLM_ENFORCE_EAGER" = "1" ]; then
  VLLM_ARGS+=(--enforce-eager)
fi

nohup python3 -m vllm.entrypoints.openai.api_server \
  "${VLLM_ARGS[@]}" \
  >"$LOG_DIR/vllm.log" 2>&1 &

log "Waiting for vLLM (log: ${LOG_DIR}/vllm.log) — 31B load can take several minutes…"
for i in $(seq 1 180); do
  if curl -sf "http://127.0.0.1:${VLLM_PORT}/v1/models" >/dev/null 2>&1; then
    log "Ready."
    curl -sf "http://127.0.0.1:${VLLM_PORT}/v1/models"
    echo
    exit 0
  fi
  if ! pgrep -f "vllm.entrypoints.openai.api_server" >/dev/null 2>&1; then
    if [ "$i" -gt 12 ]; then
      dump_vllm_errors "$LOG_DIR/vllm.log"
      die "vLLM process exited — see ${LOG_DIR}/vllm.log"
    fi
  fi
  if [ "$((i % 12))" -eq 0 ]; then
    log "  still loading… (${i}×5s)"
    tail -3 "$LOG_DIR/vllm.log" 2>/dev/null || true
  fi
  sleep 5
done

dump_vllm_errors "$LOG_DIR/vllm.log"
die "vLLM not ready after 900s — see ${LOG_DIR}/vllm.log"
