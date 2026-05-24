#!/usr/bin/env bash
# Upsert LLM keys in .env (no manual editing on RunPod).
#
# Usage:
#   bash scripts/sync_env_llm.sh
#   bash scripts/sync_env_llm.sh /path/to/.env

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="${1:-$REPO_DIR/.env}"

VLLM_PORT=${VLLM_PORT:-8000}
VLLM_SERVED_NAME=${VLLM_SERVED_NAME:-gemma4-31b}

set_env_key() {
  local file=$1 key=$2 val=$3
  touch "$file"
  if grep -q "^${key}=" "$file" 2>/dev/null; then
    sed -i "s|^${key}=.*|${key}=${val}|" "$file"
  else
    echo "${key}=${val}" >> "$file"
  fi
}

echo "[sync_env] Updating ${ENV_FILE}"
set_env_key "$ENV_FILE" SHORTS_PLANNER_BACKEND vllm
set_env_key "$ENV_FILE" SHORTS_LOCAL_LLM_BASE_URL "http://127.0.0.1:${VLLM_PORT}/v1"
set_env_key "$ENV_FILE" SHORTS_LOCAL_LLM_MODEL "$VLLM_SERVED_NAME"
set_env_key "$ENV_FILE" SHORTS_LOCAL_LLM_TIMEOUT_S 600
set_env_key "$ENV_FILE" SHORTS_LOCAL_LLM_TEMPERATURE 0.4
set_env_key "$ENV_FILE" SHORTS_LOCAL_LLM_MAX_TOKENS 8000
echo "[sync_env] Done."
