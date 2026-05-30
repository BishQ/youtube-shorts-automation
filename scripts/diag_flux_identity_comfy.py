"""Diagnose ComfyUI flux2_dev_identity_full readiness on RunPod/local.

Checks that all workflow node types exist in /object_info.
Does NOT queue the raw workflow JSON (placeholder ref_1.png files are invalid).

Usage:
  python scripts/diag_flux_identity_comfy.py
  python scripts/diag_flux_identity_comfy.py --try-prompt   # also POST unpatched workflow (expect 400)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if SRC.exists():
    sys.path.insert(0, str(SRC))

from shorts_pipeline.config.settings import get_settings
from shorts_pipeline.image_worker.flux_identity_generator import load_flux_identity_bundle

WF = ROOT / "workflows" / "flux2_dev_identity_full.json"


def main() -> int:
    p = argparse.ArgumentParser(description="Check flux_identity ComfyUI node registration")
    p.add_argument(
        "--try-prompt",
        action="store_true",
        help="Also POST raw workflow (fails unless ref_*.png exist on server — not a real readiness test)",
    )
    args = p.parse_args()

    s = get_settings()
    base = s.comfy_base_url.rstrip("/")
    bundle = load_flux_identity_bundle(WF)
    wf = dict(bundle.prompt)

    with httpx.Client(timeout=60.0) as client:
        r = client.get(f"{base}/object_info")
        r.raise_for_status()
        obj = r.json()
        class_types = {
            n["class_type"] for n in wf.values() if isinstance(n, dict) and "class_type" in n
        }
        missing = sorted(ct for ct in class_types if ct not in obj)
        print(f"ComfyUI: {base}")
        print(f"Workflow nodes: {len(class_types)}")
        if missing:
            print("MISSING node types on server:")
            for m in missing:
                print(f"  - {m}")
            return 1

        print("All workflow node types registered on server.")
        print("Ready for flux_identity jobs (pipeline uploads ref photos before /prompt).")

        if not args.try_prompt:
            return 0

        payload = {"prompt": wf, "client_id": "diag-flux-identity"}
        pr = client.post(f"{base}/prompt", json=payload)
        print(f"POST /prompt (unpatched placeholders) -> HTTP {pr.status_code}")
        print(pr.text[:4000])
        if pr.status_code >= 400:
            print(
                "\nNote: 400 here is normal — ref_1.png / face_mask.png are workflow placeholders. "
                "Real jobs upload images via /upload/image first."
            )
        return 0 if pr.status_code < 400 else 0


if __name__ == "__main__":
    raise SystemExit(main())
