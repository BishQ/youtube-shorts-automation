#!/usr/bin/env bash
# Start vLLM OpenAI API server (Gemma 4 31B on RunPod by default).
#
# Usage:
#   bash scripts/start_vllm.sh
#
# Env overrides:
#   VLLM_MODEL=/workspace/models/gemma4-31b
#   VLLM_SERVED_NAME=gemma4-31b
#   VLLM_PORT=8000
#   VLLM_HOST=0.0.0.0
#   VLLM_MAX_NUM_BATCHED_TOKENS=8192   # Gemma 4 MM needs >2496 (vLLM default 2048)
#   VLLM_GPU_MEMORY_UTILIZATION=0.90

set -euo pipefail

ROOT=${ROOT:-/workspace}
LOG_DIR=${LOG_DIR:-$ROOT/logs}
mkdir -p "$LOG_DIR"

VLLM_MODEL=${VLLM_MODEL:-/workspace/models/gemma4-31b}
VLLM_SERVED_NAME=${VLLM_SERVED_NAME:-gemma4-31b}
VLLM_PORT=${VLLM_PORT:-8000}
VLLM_HOST=${VLLM_HOST:-0.0.0.0}
VLLM_MAX_NUM_BATCHED_TOKENS=${VLLM_MAX_NUM_BATCHED_TOKENS:-8192}
VLLM_GPU_MEMORY_UTILIZATION=${VLLM_GPU_MEMORY_UTILIZATION:-0.90}
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

log() { echo -e "\n\033[1;36m[vllm]\033[0m $*"; }
warn() { echo -e "\033[1;33m[vllm]\033[0m $*" >&2; }
die() { echo -e "\033[1;31m[vllm]\033[0m $*" >&2; exit 1; }

if curl -sf "http://127.0.0.1:${VLLM_PORT}/v1/models" >/dev/null 2>&1; then
  log "Already running on port ${VLLM_PORT}."
  curl -sf "http://127.0.0.1:${VLLM_PORT}/v1/models"
  echo
  exit 0
fi

if [ ! -e "$VLLM_MODEL" ]; then
  die "Model not found: ${VLLM_MODEL} — set VLLM_MODEL to your local weights path"
fi

log "Installing vLLM (if needed)…"
pip install -q vllm

if [ -f "$REPO_DIR/scripts/sync_env_llm.sh" ]; then
  VLLM_PORT="$VLLM_PORT" VLLM_SERVED_NAME="$VLLM_SERVED_NAME" \
    bash "$REPO_DIR/scripts/sync_env_llm.sh" "$REPO_DIR/.env"
fi

log "Starting: model=${VLLM_MODEL} served=${VLLM_SERVED_NAME} port=${VLLM_PORT} max_batched=${VLLM_MAX_NUM_BATCHED_TOKENS}"
pkill -f "vllm.entrypoints.openai.api_server" || true
sleep 1

export HF_HOME="${HF_HOME:-$ROOT/hf_cache}"
mkdir -p "$HF_HOME"

nohup python3 -m vllm.entrypoints.openai.api_server \
  --model "$VLLM_MODEL" \
  --served-model-name "$VLLM_SERVED_NAME" \
  --port "$VLLM_PORT" \
  --host "$VLLM_HOST" \
  --max-num-batched-tokens "$VLLM_MAX_NUM_BATCHED_TOKENS" \
  --gpu-memory-utilization "$VLLM_GPU_MEMORY_UTILIZATION" \
  --trust-remote-code \
  >"$LOG_DIR/vllm.log" 2>&1 &

log "Waiting for vLLM (log: ${LOG_DIR}/vllm.log)…"
for i in $(seq 1 120); do
  if curl -sf "http://127.0.0.1:${VLLM_PORT}/v1/models" >/dev/null 2>&1; then
    log "Ready."
    curl -sf "http://127.0.0.1:${VLLM_PORT}/v1/models"
    echo
    exit 0
  fi
  if ! pgrep -f "vllm.entrypoints.openai.api_server" >/dev/null 2>&1; then
    if [ "$i" -gt 3 ]; then
      warn "vLLM process exited — last 40 log lines:"
      tail -40 "$LOG_DIR/vllm.log" >&2 || true
      die "vLLM failed to start — see ${LOG_DIR}/vllm.log"
    fi
  fi
  sleep 5
done

tail -40 "$LOG_DIR/vllm.log" >&2 || true
die "vLLM not ready after 600s — see ${LOG_DIR}/vllm.log"
