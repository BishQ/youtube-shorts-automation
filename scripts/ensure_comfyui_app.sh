#!/usr/bin/env bash
# Ensure ComfyUI application files exist under COMFYUI_ROOT (keeps models/ + custom_nodes/).
#
# RunPod ComfyUI template usually ships main.py on container disk. After a pod
# restart, /workspace/ComfyUI may only contain bootstrap artifacts (models,
# custom_nodes) without main.py — this script restores the app on the volume.
#
# Usage:
#   export COMFYUI_ROOT=/workspace/ComfyUI
#   bash scripts/ensure_comfyui_app.sh
#
set -euo pipefail

COMFYUI_ROOT="${COMFYUI_ROOT:-/workspace/ComfyUI}"
COMFYUI_REPO="${COMFYUI_REPO:-https://github.com/comfyanonymous/ComfyUI.git}"
COMFYUI_REF="${COMFYUI_REF:-master}"

if [[ -f "$COMFYUI_ROOT/main.py" ]]; then
  echo "ComfyUI app OK: $COMFYUI_ROOT/main.py"
  exit 0
fi

echo "Missing $COMFYUI_ROOT/main.py — cloning ComfyUI app (models/custom_nodes untouched) ..."
mkdir -p "$COMFYUI_ROOT"
tmpdir="$(mktemp -d /tmp/comfyui_clone.XXXXXX)"
trap 'rm -rf "$tmpdir"' EXIT

git clone --depth 1 --branch "$COMFYUI_REF" "$COMFYUI_REPO" "$tmpdir"

echo "Copying app files into $COMFYUI_ROOT (skip models/, custom_nodes/, output/) ..."
shopt -s dotglob
for item in "$tmpdir"/*; do
  base="$(basename "$item")"
  case "$base" in
    models|custom_nodes|output|.git) continue ;;
  esac
  if [[ -e "$COMFYUI_ROOT/$base" ]]; then
    continue
  fi
  cp -a "$item" "$COMFYUI_ROOT/$base"
done
shopt -u dotglob

if [[ ! -f "$COMFYUI_ROOT/main.py" ]]; then
  echo "ERROR: clone finished but main.py still missing" >&2
  exit 1
fi

PY=""
for candidate in \
  "$COMFYUI_ROOT/.venv/bin/python" \
  "$COMFYUI_ROOT/venv/bin/python" \
  python3 \
  python; do
  if [[ -x "$candidate" ]] || command -v "$candidate" >/dev/null 2>&1; then
    PY="$candidate"
    break
  fi
done
if [[ -z "$PY" ]]; then
  echo "No python found to install requirements" >&2
  exit 127
fi

echo "Installing ComfyUI requirements with $PY ..."
"$PY" -m pip install -q -U pip
"$PY" -m pip install -q -r "$COMFYUI_ROOT/requirements.txt"

echo "Done. Start with: bash scripts/start_comfyui.sh --restart"
