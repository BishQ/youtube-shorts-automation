"""Upscale any unprocessed Grok raw images for a given job.

For each clause_NNN.png in images/_grok_raw/ where images/clause_NNN.png
doesn't exist, run the ComfyUI upscale workflow.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import os
_env = ROOT / ".env"
if _env.is_file():
    for line in _env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ: os.environ[k] = v

from shorts_pipeline.config.settings import Settings
from shorts_pipeline.config.ui_store import effective_settings
from shorts_pipeline.image_worker.comfy import ComfyClient, load_upscale_bundle


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("job_id")
    args = ap.parse_args()
    s = effective_settings(Settings())
    img_dir = s.data_dir / "jobs" / args.job_id / "images"
    raw_dir = img_dir / "_grok_raw"
    if not raw_dir.is_dir():
        print(f"No _grok_raw in {img_dir}"); return 1

    up_path = (s.workflows_dir / f"{s.comfy_upscale_workflow_name}.json").resolve()
    up_bundle = load_upscale_bundle(up_path)
    comfy = ComfyClient(s)

    raws = sorted(raw_dir.glob("clause_*.png"))
    todo = [r for r in raws if not (img_dir / r.name).exists()]
    print(f"Found {len(raws)} raws, {len(todo)} need upscale")
    for r in todo:
        out = img_dir / r.name
        print(f"  upscale: {r.name} ...", end=" ", flush=True)
        comfy.upscale_image(up_bundle, r, out)
        print(f"OK ({out.stat().st_size//1024} KB)")
    print(f"\nDone — {len(list(img_dir.glob('clause_*.png')))} final images in {img_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
