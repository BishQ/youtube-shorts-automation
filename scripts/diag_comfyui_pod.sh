#!/usr/bin/env bash
# Quick ComfyUI startup diagnostics on RunPod (paste after bootstrap).
set -u

COMFYUI_ROOT="${COMFYUI_ROOT:-/workspace/ComfyUI}"
PORT="${COMFYUI_PORT:-8188}"
LOG="${COMFYUI_LOG_DIR:-/workspace/logs}/comfyui.log"

echo "=== ComfyUI pod diagnostic ==="
echo "COMFYUI_ROOT=$COMFYUI_ROOT"
echo

if [[ ! -f "$COMFYUI_ROOT/main.py" ]]; then
  echo "ERROR: missing $COMFYUI_ROOT/main.py"
  exit 1
fi

echo "-- Port ${PORT} --"
if command -v ss >/dev/null 2>&1; then
  ss -tlnp | grep ":${PORT} " || echo "(nothing listening on ${PORT})"
fi

echo
echo "-- Python candidates --"
for py in \
  "$COMFYUI_ROOT/.venv/bin/python" \
  "$COMFYUI_ROOT/venv/bin/python" \
  /opt/conda/bin/python \
  python3 \
  python; do
  if [[ -x "$py" ]] || command -v "$py" >/dev/null 2>&1; then
    echo -n "  $py -> "
    "$py" --version 2>&1 || true
  fi
done

echo
echo "-- Custom nodes on disk --"
ls -1 "$COMFYUI_ROOT/custom_nodes/" 2>/dev/null || echo "(none)"

echo
echo "-- Last 40 lines of log ($LOG) --"
tail -n 40 "$LOG" 2>/dev/null || echo "(no log yet)"

echo
echo "-- Try 15s foreground start (shows import errors) --"
cd "$COMFYUI_ROOT"
PY=""
for candidate in "$COMFYUI_ROOT/.venv/bin/python" "$COMFYUI_ROOT/venv/bin/python" python3 python; do
  if [[ -x "$candidate" ]] || command -v "$candidate" >/dev/null 2>&1; then
    PY="$candidate"
    break
  fi
done
if [[ -z "$PY" ]]; then
  echo "No python found"
  exit 1
fi
echo "Using: $PY"
timeout 15s "$PY" main.py --listen 0.0.0.0 --port "$PORT" --enable-cors-header 2>&1 | tail -n 30 || true

echo
echo "If you see ImportError above, run:"
echo "  python3 -m pip install insightface onnxruntime-gpu open-clip-torch safetensors 'ml_dtypes==0.3.2'"
echo "Then: bash scripts/start_comfyui.sh --restart"
