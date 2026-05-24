#!/usr/bin/env bash
# Start Ollama (simple fallback — usually works first try on RunPod).
#   bash scripts/start_ollama.sh

set -euo pipefail

OLLAMA_MODEL=${OLLAMA_MODEL:-qwen2.5:32b}
OLLAMA_PORT=${OLLAMA_PORT:-11434}

log() { echo -e "\n\033[1;32m[ollama]\033[0m $*"; }

if curl -sf "http://127.0.0.1:${OLLAMA_PORT}/v1/models" >/dev/null 2>&1; then
  log "Already running."
  curl -sf "http://127.0.0.1:${OLLAMA_PORT}/v1/models"; echo
  exit 0
fi

if ! command -v ollama >/dev/null 2>&1; then
  log "Installing Ollama…"
  curl -fsSL https://ollama.com/install.sh | sh
fi

pkill -f "ollama serve" 2>/dev/null || true
sleep 1
OLLAMA_HOST="127.0.0.1:${OLLAMA_PORT}" nohup ollama serve >/workspace/logs/ollama.log 2>&1 &
sleep 3

log "Pulling ${OLLAMA_MODEL} (skip if cached)…"
ollama pull "$OLLAMA_MODEL"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$REPO_DIR/.env"
touch "$ENV_FILE"
set_kv() { grep -q "^$1=" "$ENV_FILE" && sed -i "s|^$1=.*|$1=$2|" "$ENV_FILE" || echo "$1=$2" >>"$ENV_FILE"; }
set_kv SHORTS_PLANNER_BACKEND ollama
set_kv SHORTS_LOCAL_LLM_BASE_URL "http://127.0.0.1:${OLLAMA_PORT}/v1"
set_kv SHORTS_LOCAL_LLM_MODEL "$OLLAMA_MODEL"

log "Ready — set SHORTS_PLANNER_BACKEND=ollama in .env"
curl -sf "http://127.0.0.1:${OLLAMA_PORT}/v1/models"; echo
