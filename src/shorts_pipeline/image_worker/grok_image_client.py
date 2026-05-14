"""xAI Grok Imagine image generation (OpenAI-compatible /v1/images/generations)."""

from __future__ import annotations

import base64
from typing import Any

import httpx

from shorts_pipeline.config.settings import Settings
from shorts_pipeline.logging_setup import get_logger

log = get_logger(__name__)


class GrokImageError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None, detail: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail


def build_grok_image_client(settings: Settings) -> GrokImagineClient:
    if not settings.grok_api_key:
        raise GrokImageError(
            "grok_api_key is not set. Add SHORTS_GROK_API_KEY in .env "
            "(required for image_backend=hybrid_grok_bookends)."
        )
    return GrokImagineClient(settings)


class GrokImagineClient:
    """POST https://api.x.ai/v1/images/generations — returns PNG bytes."""

    def __init__(self, settings: Settings) -> None:
        self._s = settings

    def _url(self) -> str:
        base = self._s.grok_api_base_url.rstrip("/")
        return f"{base}/images/generations"

    def generate(self, prompt: str) -> bytes:
        body: dict[str, Any] = {
            "model": self._s.grok_image_model,
            "prompt": prompt,
            "n": 1,
            "response_format": "b64_json",
            "aspect_ratio": "9:16",
        }
        headers = {
            "Authorization": f"Bearer {self._s.grok_api_key}",
            "Content-Type": "application/json",
        }
        try:
            with httpx.Client(timeout=self._s.grok_timeout_s) as client:
                r = client.post(self._url(), headers=headers, json=body)
        except httpx.TimeoutException as e:
            raise GrokImageError(f"Grok image request timeout: {e}") from e
        except httpx.RequestError as e:
            raise GrokImageError(f"Grok image connection error: {e}") from e

        if r.status_code >= 400:
            raise GrokImageError(
                f"Grok image HTTP {r.status_code}",
                status_code=r.status_code,
                detail=r.text[:4000],
            )

        data = r.json()
        items = data.get("data")
        if not isinstance(items, list) or not items:
            raise GrokImageError("Grok image response missing data[]", detail=data)

        first = items[0]
        if not isinstance(first, dict):
            raise GrokImageError("Grok image invalid data[0]", detail=data)

        b64 = first.get("b64_json")
        if isinstance(b64, str) and b64.strip():
            return base64.b64decode(b64)

        url = first.get("url")
        if isinstance(url, str) and url.strip():
            log.info("grok_image_downloading_url", preview=url[:80])
            with httpx.Client(timeout=self._s.grok_timeout_s) as dl:
                img = dl.get(url.strip())
            if img.status_code >= 400:
                raise GrokImageError(
                    f"Grok image URL download HTTP {img.status_code}",
                    status_code=img.status_code,
                    detail=img.text[:2000],
                )
            return img.content

        raise GrokImageError("Grok image response has neither b64_json nor url", detail=first)
