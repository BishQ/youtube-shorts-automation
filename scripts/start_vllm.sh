#!/usr/bin/env bash
# Start vLLM for Gemma 4 31B (text-only Shorts planner).
# Auto-tunes for 1×80GB vs 2×40GB. Ollama was simpler; this matches official vLLM recipe.
#
#   bash scripts/start_vllm.sh

set -euo pipefail

ROOT=${ROOT:-/workspace}
LOG_DIR=${LOG_DIR:-$ROOT/logs}
mkdir -p "$LOG_DIR"

VLLM_MODEL=${VLLM_MODEL:-/workspace/models/gemma4-31b}
VLLM_SERVED_NAME=${VLLM_SERVED_NAME:-gemma4-31b}
VLLM_PORT=${VLLM_PORT:-8000}
VLLM_HOST=${VLLM_HOST:-0.0.0.0}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

log() { echo -e "\n\033[1;36m[vllm]\033[0m $*"; }
warn() { echo -e "\033[1;33m[vllm]\033[0m $*" >&2; }
die() { echo -e "\033[1;31m[vllm]\033[0m $*" >&2; exit 1; }

dump_vllm_errors() {
  warn "=== nvidia-smi ==="
  nvidia-smi 2>/dev/null || true
  warn "=== vllm.log (last errors) ==="
  grep -iE 'error|oom|cuda|failed|traceback|runtimeerror|valueerror' \
    "$LOG_DIR/vllm.log" 2>/dev/null | tail -60 \
    || tail -60 "$LOG_DIR/vllm.log" 2>/dev/null || true
}

gpu_count() {
  nvidia-smi -L 2>/dev/null | wc -l || echo 1
}

gpu_vram_mb() {
  nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null \
    | head -1 | tr -d ' ' || echo 0
}

# Pick flags from GPU — 80GB single-GPU uses the official simple recipe (no fp8, no eager).
apply_vram_profile() {
  local gpus vram
  gpus=$(gpu_count)
  vram=$(gpu_vram_mb)

  if [ -n "${VLLM_TENSOR_PARALLEL_SIZE:-}" ]; then
    : # user override
  elif [ "$gpus" -ge 2 ]; then
    VLLM_TENSOR_PARALLEL_SIZE=2
  else
    VLLM_TENSOR_PARALLEL_SIZE=1
  fi

  # Defaults (override via env any time)
  VLLM_MAX_MODEL_LEN=${VLLM_MAX_MODEL_LEN:-8192}
  VLLM_MAX_NUM_BATCHED_TOKENS=${VLLM_MAX_NUM_BATCHED_TOKENS:-8192}
  VLLM_LIMIT_MM=${VLLM_LIMIT_MM:-'{"image": 0, "audio": 0, "video": 0}'}

  if [ "$vram" -ge 75000 ] && [ "$gpus" -eq 1 ]; then
    log "Profile: 1×80GB — BF16, no fp8 KV (Gemma 4 safe on H100/A100-80G)"
    VLLM_GPU_MEMORY_UTILIZATION=${VLLM_GPU_MEMORY_UTILIZATION:-0.90}
    VLLM_KV_CACHE_DTYPE=${VLLM_KV_CACHE_DTYPE:-auto}
    VLLM_ENFORCE_EAGER=${VLLM_ENFORCE_EAGER:-0}
  elif [ "$gpus" -ge 2 ]; then
    log "Profile: ${gpus} GPUs — tensor-parallel-size=${VLLM_TENSOR_PARALLEL_SIZE}"
    VLLM_GPU_MEMORY_UTILIZATION=${VLLM_GPU_MEMORY_UTILIZATION:-0.90}
    VLLM_KV_CACHE_DTYPE=${VLLM_KV_CACHE_DTYPE:-auto}
    VLLM_ENFORCE_EAGER=${VLLM_ENFORCE_EAGER:-0}
  else
    log "Profile: low VRAM — fp8 KV + eager mode"
    VLLM_GPU_MEMORY_UTILIZATION=${VLLM_GPU_MEMORY_UTILIZATION:-0.92}
    VLLM_KV_CACHE_DTYPE=${VLLM_KV_CACHE_DTYPE:-fp8}
    VLLM_ENFORCE_EAGER=${VLLM_ENFORCE_EAGER:-1}
  fi
}

if curl -sf "http://127.0.0.1:${VLLM_PORT}/v1/models" >/dev/null 2>&1; then
  log "Already running."
  curl -sf "http://127.0.0.1:${VLLM_PORT}/v1/models"; echo
  exit 0
fi

[ -e "$VLLM_MODEL" ] || die "Model not found: ${VLLM_MODEL}"

apply_vram_profile

log "Installing vLLM…"
pip install -q -U 'vllm>=0.8.0'

[ -f "$REPO_DIR/scripts/sync_env_llm.sh" ] && \
  VLLM_PORT="$VLLM_PORT" VLLM_SERVED_NAME="$VLLM_SERVED_NAME" \
  bash "$REPO_DIR/scripts/sync_env_llm.sh" "$REPO_DIR/.env"

log "Launching Gemma 4 text-only"
log "  model=${VLLM_MODEL}"
log "  tp=${VLLM_TENSOR_PARALLEL_SIZE}  port=${VLLM_PORT}  vram=$(gpu_vram_mb)MB  gpus=$(gpu_count)"

pkill -f "vllm.entrypoints.openai.api_server" || true
sleep 2
export HF_HOME="${HF_HOME:-$ROOT/hf_cache}"
mkdir -p "$HF_HOME"

ARGS=(
  --model "$VLLM_MODEL"
  --served-model-name "$VLLM_SERVED_NAME"
  --host "$VLLM_HOST"
  --port "$VLLM_PORT"
  --tensor-parallel-size "$VLLM_TENSOR_PARALLEL_SIZE"
  --max-model-len "$VLLM_MAX_MODEL_LEN"
  --max-num-batched-tokens "$VLLM_MAX_NUM_BATCHED_TOKENS"
  --gpu-memory-utilization "$VLLM_GPU_MEMORY_UTILIZATION"
  --limit-mm-per-prompt "$VLLM_LIMIT_MM"
  --trust-remote-code
)
[ "$VLLM_KV_CACHE_DTYPE" != "auto" ] && ARGS+=(--kv-cache-dtype "$VLLM_KV_CACHE_DTYPE")
[ "$VLLM_ENFORCE_EAGER" = "1" ] && ARGS+=(--enforce-eager)

nohup python3 -m vllm.entrypoints.openai.api_server "${ARGS[@]}" \
  >"$LOG_DIR/vllm.log" 2>&1 &

log "Loading weights… (2–8 min on 31B) — tail -f $LOG_DIR/vllm.log"
for i in $(seq 1 180); do
  curl -sf "http://127.0.0.1:${VLLM_PORT}/v1/models" >/dev/null 2>&1 && {
    log "Ready."
    curl -sf "http://127.0.0.1:${VLLM_PORT}/v1/models"; echo
    exit 0
  }
  if ! pgrep -f "vllm.entrypoints.openai.api_server" >/dev/null 2>&1 && [ "$i" -gt 15 ]; then
    dump_vllm_errors
    die "vLLM crashed — see $LOG_DIR/vllm.log"
  fi
  [ "$((i % 12))" -eq 0 ] && log "  …still loading (${i}×5s)"
  sleep 5
done
dump_vllm_errors
die "Timeout — see $LOG_DIR/vllm.log"
