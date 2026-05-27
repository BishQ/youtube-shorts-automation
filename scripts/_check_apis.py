"""One-off: check Grok/Together env without printing secrets."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from shorts_pipeline.config.settings import Settings
from shorts_pipeline.image_worker.smart_grok_image_generator import _together_available

s = Settings()
print("image_backend:", s.image_backend)
print("grok_api_key_set:", bool(s.grok_api_key))
print("flux_api_key_set:", bool(s.flux_api_key))
print("flux_api_provider:", s.flux_api_provider)
print("flux_api_model:", s.flux_api_model)
print("together_available:", _together_available(s))
print("grok_model:", s.grok_image_model)

if _together_available(s):
    try:
        from shorts_pipeline.image_worker.flux_api_client import TogetherFluxImageClient

        client = TogetherFluxImageClient(s)
        data = client.generate("Test: ancient stone amphitheater at golden dusk, wide shot, soft light.")
        print("together_test_ok: bytes=", len(data))
    except Exception as e:
        print("together_test_fail:", type(e).__name__, str(e)[:300])
else:
    print("together_test_skipped: provider or key not configured for Together")

if s.grok_api_key:
    try:
        from shorts_pipeline.image_worker.grok_image_client import build_grok_image_client

        grok = build_grok_image_client(s)
        data = grok.generate(
            "Photorealistic vertical 9:16 cinematic still. Scene: bronze helmet on sand, wide shot, sunset light."
        )
        print("grok_test_ok: bytes=", len(data))
    except Exception as e:
        print("grok_test_fail:", type(e).__name__, str(e)[:300])
        detail = getattr(e, "detail", None)
        if detail:
            print("grok_test_detail:", str(detail)[:500])
else:
    print("grok_test_skipped: no SHORTS_GROK_API_KEY")
