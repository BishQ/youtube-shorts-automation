#!/usr/bin/env bash
# Start vLLM OpenAI API server (Qwen3.6-27B on RunPod).
#
# Usage:
#   bash scripts/start_vllm.sh
#
# Env overrides:
#   VLLM_MODEL=/workspace/models/qwen3.6-27b
#   VLLM_SERVED_NAME=qwen3-27b
#   VLLM_PORT=8000
#   VLLM_TENSOR_PARALLEL_SIZE=2      # auto = GPU count (31B needs 80GB or TP>=2)
#   VLLM_MAX_MODEL_LEN=11264
#   VLLM_MAX_NUM_BATCHED_TOKENS=11264
#   VLLM_GPU_MEMORY_UTILIZATION=0.92
#   VLLM_KV_CACHE_DTYPE=auto         # A100-safe default; override only if supported
#   VLLM_LIMIT_MM='{"image":0,"audio":0,"video":0}'  # text-only (Shorts planner)

set -euo pipefail

ROOT=${ROOT:-/workspace}
LOG_DIR=${LOG_DIR:-$ROOT/logs}
mkdir -p "$LOG_DIR"

VLLM_MODEL=${VLLM_MODEL:-/workspace/models/qwen3.6-27b}
VLLM_SERVED_NAME=${VLLM_SERVED_NAME:-qwen3-27b}
VLLM_PORT=${VLLM_PORT:-8000}
VLLM_HOST=${VLLM_HOST:-0.0.0.0}
VLLM_MAX_NUM_BATCHED_TOKENS=${VLLM_MAX_NUM_BATCHED_TOKENS:-11264}
VLLM_MAX_MODEL_LEN=${VLLM_MAX_MODEL_LEN:-11264}
VLLM_GPU_MEMORY_UTILIZATION=${VLLM_GPU_MEMORY_UTILIZATION:-0.92}
VLLM_KV_CACHE_DTYPE=${VLLM_KV_CACHE_DTYPE:-auto}
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
if [ "$GPUS" -lt 2 ] && echo "$VLLM_MODEL" | grep -qiE '27b|32b' && ! echo "$VLLM_MODEL" | grep -qi 'awq\|gptq\|int4\|int8'; then
  warn "Qwen3.6-27B BF16 needs ~54GB VRAM — fits single A100 80GB with reduced KV cache."
fi

ensure_cuda_dev_headers() {
  # FlashInfer JIT needs curand.h; RunPod images often ship runtime-only CUDA.
  if [ -f /usr/local/cuda/include/curand.h ]; then
    return 0
  fi
  log "Installing CUDA dev headers (curand) for FlashInfer…"
  apt-get update -qq 2>/dev/null || true
  apt-get install -y -qq \
    build-essential ninja-build \
    cuda-curand-dev-12-4 cuda-curand-dev-12-6 cuda-curand-dev-12-8 \
    libcurand-dev 2>/dev/null || true
  pip install -q nvidia-curand-cu12 2>/dev/null || true
  if [ -f /usr/local/cuda/include/curand.h ]; then
    log "  curand.h found."
    return 0
  fi
  # Search pip-installed nvidia curand headers
  local curand_inc
  curand_inc=$(python3 - <<'PY' 2>/dev/null || true
import glob
hits = glob.glob("/usr/local/lib/python*/dist-packages/nvidia/curand/include/curand.h")
print(hits[0] if hits else "")
PY
)
  if [ -n "$curand_inc" ]; then
    export CPATH="$(dirname "$curand_inc")${CPATH:+:$CPATH}"
    export C_INCLUDE_PATH="$CPATH"
    log "  curand.h via pip: $(dirname "$curand_inc")"
    return 0
  fi
  warn "curand.h still missing — disabling FlashInfer sampler (VLLM_USE_FLASHINFER_SAMPLER=0)."
  return 1
}

# Avoid FlashInfer JIT sampling compile on minimal CUDA images (needs curand.h).
export VLLM_USE_FLASHINFER_SAMPLER=${VLLM_USE_FLASHINFER_SAMPLER:-0}
ensure_cuda_dev_headers || true
# Stale failed JIT cache can block retries after fixes.
rm -rf /root/.cache/flashinfer 2>/dev/null || true

export CUDA_HOME="${CUDA_HOME:-/usr/local/cuda}"
export PATH="$CUDA_HOME/bin:${PATH}"

log "Installing/upgrading vLLM…"
pip install -q -U vllm ninja

if [ -f "$REPO_DIR/scripts/sync_env_llm.sh" ]; then
  VLLM_PORT="$VLLM_PORT" VLLM_SERVED_NAME="$VLLM_SERVED_NAME" \
    bash "$REPO_DIR/scripts/sync_env_llm.sh" "$REPO_DIR/.env"
fi

log "Starting Qwen3.6-27B…"
log "  model=${VLLM_MODEL}"
log "  served=${VLLM_SERVED_NAME}  port=${VLLM_PORT}  tp=${VLLM_TENSOR_PARALLEL_SIZE}"
log "  max_model_len=${VLLM_MAX_MODEL_LEN}  max_batched=${VLLM_MAX_NUM_BATCHED_TOKENS}"
log "  kv_cache=${VLLM_KV_CACHE_DTYPE}  limit_mm=${VLLM_LIMIT_MM}"
log "  flashinfer_sampler=${VLLM_USE_FLASHINFER_SAMPLER}"

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
  --enable-prefix-caching
  --guided-decoding-backend xgrammar
  --trust-remote-code
)

# Qwen3 text-only — skip multimodal limits (only valid for VLM checkpoints).
if echo "$VLLM_MODEL" | grep -qiE 'vl|vision|multimodal|gemma'; then
  VLLM_ARGS+=(--limit-mm-per-prompt "$VLLM_LIMIT_MM")
fi

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
