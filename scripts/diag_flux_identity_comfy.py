"""Diagnose ComfyUI /prompt 400 for flux2_dev_identity_full on RunPod."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

SRC = Path(__file__).resolve().parent / "src"
sys.path.insert(0, str(SRC))

from shorts_pipeline.config.settings import get_settings
from shorts_pipeline.image_worker.flux_identity_generator import load_flux_identity_bundle

WF = Path(__file__).resolve().parent.parent / "workflows" / "flux2_dev_identity_full.json"


def main() -> int:
    s = get_settings()
    base = s.comfy_base_url.rstrip("/")
    bundle = load_flux_identity_bundle(WF)
    wf = dict(bundle.prompt)

    with httpx.Client(timeout=60.0) as client:
        r = client.get(f"{base}/object_info")
        r.raise_for_status()
        obj = r.json()
        class_types = {n["class_type"] for n in wf.values() if isinstance(n, dict) and "class_type" in n}
        missing = sorted(ct for ct in class_types if ct not in obj)
        print(f"ComfyUI: {base}")
        print(f"Workflow nodes: {len(class_types)}")
        if missing:
            print("MISSING node types on server:")
            for m in missing:
                print(f"  - {m}")
        else:
            print("All workflow node types registered on server.")

        payload = {"prompt": wf, "client_id": "diag-flux-identity"}
        pr = client.post(f"{base}/prompt", json=payload)
        print(f"POST /prompt -> HTTP {pr.status_code}")
        print(pr.text[:4000])
    return 0 if not missing and pr.status_code < 400 else 1


if __name__ == "__main__":
    raise SystemExit(main())
