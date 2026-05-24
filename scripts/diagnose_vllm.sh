#!/usr/bin/env bash
# Quick GPU + model diagnostics before starting vLLM.
set -euo pipefail

echo "=== nvidia-smi ==="
nvidia-smi 2>/dev/null || echo "no GPU"

echo
echo "=== GPU count ==="
nvidia-smi -L 2>/dev/null || true

echo
echo "=== Model path ==="
MODEL=${VLLM_MODEL:-/workspace/models/gemma4-31b}
if [ -d "$MODEL" ]; then
  ls -lah "$MODEL" | head -20
  echo
  [ -f "$MODEL/config.json" ] && head -30 "$MODEL/config.json" || true
else
  echo "MISSING: $MODEL"
fi

echo
echo "=== vLLM process ==="
pgrep -af "vllm.entrypoints" || echo "not running"

echo
echo "=== Port 8000 ==="
curl -sf http://127.0.0.1:8000/v1/models && echo || echo "not listening"

echo
echo "=== Last vLLM errors ==="
LOG=${LOG_DIR:-/workspace/logs}/vllm.log
if [ -f "$LOG" ]; then
  grep -iE 'error|oom|cuda|failed|runtimeerror|valueerror' "$LOG" | tail -30 || tail -20 "$LOG"
else
  echo "no log at $LOG"
fi
