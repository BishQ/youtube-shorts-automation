#!/usr/bin/env bash
# Install ComfyUI-VideoHelperSuite (required by ltx23_i2v_full.json VHS_VideoCombine node).
set -euo pipefail
COMFYUI_ROOT="${COMFYUI_ROOT:-/workspace/ComfyUI}"
TARGET="${COMFYUI_ROOT}/custom_nodes/ComfyUI-VideoHelperSuite"
if [[ -d "${TARGET}/.git" ]]; then
  echo "SKIP ComfyUI-VideoHelperSuite (already at ${TARGET})"
else
  git clone --depth 1 https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git "${TARGET}"
fi
if [[ -f "${TARGET}/requirements.txt" ]]; then
  python3 -m pip install -q -r "${TARGET}/requirements.txt"
fi
echo "Done. Restart ComfyUI:"
echo "  pkill -f '${COMFYUI_ROOT}/main.py' || true"
echo "  bash scripts/start_comfyui.sh"
