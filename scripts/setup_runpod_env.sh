#!/usr/bin/env bash
# Apply RunPod .env (Step 4) — no manual editing on the pod.
#
# Usage (on RunPod after git clone):
#   bash scripts/setup_runpod_env.sh
#
# Merge Telegram + other secrets from your Windows .env upload:
#   bash scripts/setup_runpod_env.sh --merge-secrets /workspace/.env.windows
#
# Or pass secrets via environment before running:
#   export SHORTS_TG_API_ID=12345678
#   export SHORTS_TG_API_HASH=abcdef...
#   export SHORTS_TG_TARGET_CHAT=-1001234567890
#   bash scripts/setup_runpod_env.sh
#
# Override reference photos path:
#   REFERENCE_IMAGES_ROOT=/workspace/my_refs bash scripts/setup_runpod_env.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TEMPLATE="$REPO_DIR/.env.runpod"
TARGET="$REPO_DIR/.env"
MERGE_SECRETS=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --merge-secrets)
      MERGE_SECRETS="${2:-}"
      shift 2
      ;;
    -h|--help)
      sed -n '2,18p' "$0"
      exit 0
      ;;
    *)
      echo "Unknown arg: $1" >&2
      exit 1
      ;;
  esac
done

if [[ ! -f "$TEMPLATE" ]]; then
  echo "Missing template: $TEMPLATE" >&2
  exit 1
fi

log() { echo -e "\033[1;36m[runpod-env]\033[0m $*"; }

set_env_key() {
  local file=$1 key=$2 val=$3
  touch "$file"
  if grep -q "^${key}=" "$file" 2>/dev/null; then
    sed -i "s|^${key}=.*|${key}=${val}|" "$file"
  else
    echo "${key}=${val}" >> "$file"
  fi
}

read_env_val() {
  local file=$1 key=$2
  grep -E "^${key}=" "$file" 2>/dev/null | head -1 | cut -d= -f2- || true
}

log "Writing ${TARGET} from .env.runpod"
cp "$TEMPLATE" "$TARGET"

# vLLM keys (match scripts/start_vllm.sh defaults)
VLLM_PORT=${VLLM_PORT:-8000}
VLLM_SERVED_NAME=${VLLM_SERVED_NAME:-qwen3-27b}
VLLM_MODEL=${VLLM_MODEL:-/workspace/models/qwen3.6-27b}

if [[ -f "$SCRIPT_DIR/sync_env_llm.sh" ]]; then
  VLLM_PORT="$VLLM_PORT" VLLM_SERVED_NAME="$VLLM_SERVED_NAME" \
    bash "$SCRIPT_DIR/sync_env_llm.sh" "$TARGET"
else
  set_env_key "$TARGET" SHORTS_PLANNER_BACKEND vllm
  set_env_key "$TARGET" SHORTS_LOCAL_LLM_BASE_URL "http://127.0.0.1:${VLLM_PORT}/v1"
  set_env_key "$TARGET" SHORTS_LOCAL_LLM_MODEL "$VLLM_SERVED_NAME"
  set_env_key "$TARGET" SHORTS_LOCAL_LLM_STRUCTURED_OUTPUT guided_json
  set_env_key "$TARGET" SHORTS_LOCAL_LLM_MAX_TOKENS 8000
fi

if [[ -n "${REFERENCE_IMAGES_ROOT:-}" ]]; then
  set_env_key "$TARGET" SHORTS_REFERENCE_IMAGES_ROOT "$REFERENCE_IMAGES_ROOT"
fi

# Secrets from env vars (non-empty only)
for key in SHORTS_TG_API_ID SHORTS_TG_API_HASH SHORTS_TG_TARGET_CHAT SHORTS_TG_INVITE_LINK; do
  val="${!key:-}"
  if [[ -n "$val" ]]; then
    set_env_key "$TARGET" "$key" "$val"
  fi
done

# Merge Telegram (and optional HF token) from an uploaded .env
if [[ -n "$MERGE_SECRETS" && -f "$MERGE_SECRETS" ]]; then
  log "Merging secrets from ${MERGE_SECRETS}"
  for key in SHORTS_TG_API_ID SHORTS_TG_API_HASH SHORTS_TG_TARGET_CHAT SHORTS_TG_INVITE_LINK; do
    val="$(read_env_val "$MERGE_SECRETS" "$key")"
    if [[ -n "$val" ]]; then
      set_env_key "$TARGET" "$key" "$val"
    fi
  done
fi

mkdir -p "$(dirname "$(read_env_val "$TARGET" SHORTS_REFERENCE_IMAGES_ROOT)")" 2>/dev/null || \
  mkdir -p /workspace/data/famous_people_1000

log "Done. LLM model path on pod: ${VLLM_MODEL}"
log "Reference photos: $(read_env_val "$TARGET" SHORTS_REFERENCE_IMAGES_ROOT)"
log ""
log "Next:"
log "  1. Upload famous_people_1000 -> \$(grep REFERENCE_IMAGES_ROOT .env | cut -d= -f2-)"
log "  2. bash scripts/start_vllm.sh"
log "  3. bash scripts/runpod_bootstrap.sh   # ComfyUI models"
log "  4. python run_one_documentary.py"
