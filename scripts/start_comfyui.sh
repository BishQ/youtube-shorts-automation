#!/usr/bin/env bash
# Start or restart ComfyUI on a RunPod GPU pod (port 8188).
#
# Usage:
#   bash scripts/start_comfyui.sh              # start if port is free
#   bash scripts/start_comfyui.sh --restart    # kill old process, start fresh (after bootstrap)
#   bash scripts/start_comfyui.sh --check      # verify flux_identity custom nodes loaded
#
set -euo pipefail

COMFYUI_ROOT="${COMFYUI_ROOT:-/workspace/ComfyUI}"
PORT="${COMFYUI_PORT:-8188}"
LOG_DIR="${COMFYUI_LOG_DIR:-/workspace/logs}"
LOG_FILE="${LOG_DIR}/comfyui.log"
PID_FILE="${LOG_DIR}/comfyui.pid"
FORCE_RESTART=0
CHECK_ONLY=0

for arg in "$@"; do
  case "$arg" in
    --restart|-r) FORCE_RESTART=1 ;;
    --check|-c) CHECK_ONLY=1 ;;
  esac
done

mkdir -p "$LOG_DIR"

need_nodes=(
  ApplyPuLIDFlux2
  PuLIDInsightFaceLoader
  Flux2FunControlNetLoader
  InstantIDFaceAnalysis
)

check_nodes() {
  local json
  if ! json="$(curl -fsS "http://127.0.0.1:${PORT}/object_info" 2>/dev/null)"; then
    echo "ComfyUI not reachable on :${PORT}"
    return 1
  fi
  NEED_NODES="$(IFS=,; echo "${need_nodes[*]}")" python3 -c "
import json, os, sys
need = os.environ['NEED_NODES'].split(',')
data = json.loads(sys.stdin.read())
missing = [n for n in need if n not in data]
if missing:
    print('MISSING custom nodes:')
    for n in missing:
        print(f'  - {n}')
    sys.exit(1)
print('OK: flux_identity custom nodes loaded')
" <<< "$json"
}

stop_comfy() {
  echo "Stopping anything on :${PORT} ..."
  if command -v fuser >/dev/null 2>&1; then
    fuser -k "${PORT}/tcp" 2>/dev/null || true
  fi
  pkill -f "${COMFYUI_ROOT}/main.py" 2>/dev/null || true
  pkill -f "ComfyUI/main.py" 2>/dev/null || true
  pkill -f "main.py --listen 0.0.0.0 --port ${PORT}" 2>/dev/null || true
  sleep 2
  if curl -fsS "http://127.0.0.1:${PORT}/system_stats" >/dev/null 2>&1; then
    echo "Port :${PORT} still busy. Try: fuser -k ${PORT}/tcp" >&2
    ss -tlnp | grep ":${PORT} " || true
    return 1
  fi
  echo "Port :${PORT} is free."
}

start_comfy() {
  if [[ ! -f "$COMFYUI_ROOT/main.py" ]]; then
    echo "ComfyUI app missing at $COMFYUI_ROOT/main.py" >&2
    echo "Run: bash scripts/ensure_comfyui_app.sh" >&2
    exit 1
  fi
  PYTHON=""
  for candidate in \
    "$COMFYUI_ROOT/.venv/bin/python" \
    "$COMFYUI_ROOT/venv/bin/python" \
    python3 \
    python; do
    if [[ -x "$candidate" ]] || command -v "$candidate" >/dev/null 2>&1; then
      PYTHON="$candidate"
      break
    fi
  done
  if [[ -z "$PYTHON" ]]; then
    echo "python3/python not found in PATH" >&2
    exit 127
  fi
  echo "Using Python: $PYTHON ($("$PYTHON" --version 2>&1))"
  cd "$COMFYUI_ROOT"
  : > "$LOG_FILE"
  nohup "$PYTHON" main.py \
    --listen 0.0.0.0 \
    --port "$PORT" \
    --enable-cors-header \
    >> "$LOG_FILE" 2>&1 &
  echo $! > "$PID_FILE"
  echo "Starting ComfyUI pid $(cat "$PID_FILE") ..."
  for _ in $(seq 1 90); do
    if curl -fsS "http://127.0.0.1:${PORT}/system_stats" >/dev/null 2>&1; then
      echo "ComfyUI ready on :${PORT}"
      echo "Log: ${LOG_FILE}"
      return 0
    fi
    sleep 2
  done
  echo "ComfyUI failed to start. Last log lines:" >&2
  tail -n 60 "$LOG_FILE" >&2 || true
  return 1
}

if [[ "$CHECK_ONLY" -eq 1 ]]; then
  check_nodes
  exit $?
fi

if curl -fsS "http://127.0.0.1:${PORT}/system_stats" >/dev/null 2>&1; then
  if [[ "$FORCE_RESTART" -eq 0 ]] && check_nodes >/dev/null 2>&1; then
    echo "ComfyUI already running with flux_identity nodes on :${PORT}"
    exit 0
  fi
  if [[ "$FORCE_RESTART" -eq 0 ]]; then
    echo "ComfyUI is running on :${PORT} but flux_identity nodes are MISSING."
    echo "Run: bash scripts/start_comfyui.sh --restart"
    exit 2
  fi
  stop_comfy
elif [[ "$FORCE_RESTART" -eq 1 ]]; then
  stop_comfy || true
fi

start_comfy
sleep 3
check_nodes
