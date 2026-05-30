#!/usr/bin/env bash
# Install all custom nodes for workflows/ltx23_i2v_full.json (LTX 2.3 I2V).
#
# Usage on RunPod:
#   export COMFYUI_ROOT=/workspace/ComfyUI
#   bash scripts/install_ltx_i2v_nodes_on_pod.sh
#   bash scripts/start_comfyui.sh --restart
#
set -euo pipefail

COMFYUI_ROOT="${COMFYUI_ROOT:-/workspace/ComfyUI}"
CN="${COMFYUI_ROOT}/custom_nodes"
mkdir -p "$CN"

clone_node() {
  local folder="$1"
  local url="$2"
  local target="${CN}/${folder}"
  if [[ -d "${target}/.git" ]]; then
    echo "SKIP ${folder}"
    return 0
  fi
  echo "CLONE ${folder} ..."
  git clone --depth 1 "$url" "$target"
  if [[ -f "${target}/requirements.txt" ]]; then
    echo "PIP  ${folder} ..."
    python3 -m pip install -q -r "${target}/requirements.txt"
  fi
}

clone_node ComfyUI-LTXVideo https://github.com/Lightricks/ComfyUI-LTXVideo.git
clone_node ComfyUI-VideoHelperSuite https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git
clone_node ComfyUI-KJNodes https://github.com/kijai/ComfyUI-KJNodes.git
clone_node rgthree-comfy https://github.com/rgthree/rgthree-comfy.git

echo ""
echo "Done. Restart ComfyUI so nodes load:"
echo "  bash scripts/start_comfyui.sh --restart"
echo ""
echo "Verify (all should print a node name):"
echo "  curl -s http://127.0.0.1:8188/object_info | grep -oE 'VHS_VideoCombine|ImageResizeKJv2|Power Lora Loader \\(rgthree\\)'"
