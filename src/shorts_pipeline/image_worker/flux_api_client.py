"""Remote Flux image APIs for hybrid mode: BFL (async) or Together AI (sync).

BFL:  https://api.us1.bfl.ai  — X-Key header, submit + poll get_result.
Together: https://api.together.ai — Bearer token, POST /v1/images/generations.

Set SHORTS_FLUX_API_PROVIDER=together and SHORTS_FLUX_API_MODEL=black-forest-labs/FLUX.2-pro
for FLUX.2 [pro] on Together. Key is your Together API key (same as dashboard).
"""

from __future__ import annotations

import base64
import time
from typing import Any, Protocol, runtime_checkable

import httpx

from shorts_pipeline.config.settings import Settings
from shorts_pipeline.logging_setup import get_logger

log = get_logger(__name__)

_TERMINAL_STATUSES = frozenset(
    {"Ready", "Error", "Content Moderated", "Request Moderated", "Failed"}
)
_SUCCESS_STATUS = "Ready"


class FluxApiError(Exception):
    def __init__(self, message: str, *, detail: Any = None) -> None:
        super().__init__(message)
        self.detail = detail


@runtime_checkable
class FluxImageApi(Protocol):
    def generate(self, prompt: str) -> bytes: ...


class BflFluxApiClient:
    """Black Forest Labs REST API (api.us1.bfl.ai)."""

    def __init__(self, settings: Settings) -> None:
        if not settings.flux_api_key:
            raise FluxApiError(
                "flux_api_key is not set. Add SHORTS_FLUX_API_KEY in .env "
                "(required for image_backend=hybrid)."
            )
        self._s = settings
        self._headers = {
            "X-Key": settings.flux_api_key,
            "Content-Type": "application/json",
        }

    def _base(self) -> str:
        return self._s.flux_api_base_url.rstrip("/")

    def generate(self, prompt: str) -> bytes:
        task_id = self._submit(prompt)
        log.info("flux_bfl_submitted", task_id=task_id, prompt_preview=prompt[:80])
        return self._poll_and_download(task_id)

    def _submit(self, prompt: str) -> str:
        url = f"{self._base()}/v1/{self._s.flux_api_model}"
        body: dict[str, Any] = {
            "prompt": prompt,
            "width": self._s.flux_api_width,
            "height": self._s.flux_api_height,
            "safety_tolerance": 6,
            "output_format": "png",
        }
        try:
            with httpx.Client(timeout=30.0) as client:
                r = client.post(url, headers=self._headers, json=body)
        except httpx.TimeoutException as e:
            raise FluxApiError(f"BFL Flux submit timeout: {e}") from e
        except httpx.RequestError as e:
            raise FluxApiError(f"BFL Flux submit connection error: {e}") from e

        if r.status_code >= 400:
            raise FluxApiError(
                f"BFL Flux submit HTTP {r.status_code}",
                detail=r.text[:2000],
            )
        data = r.json()
        task_id = data.get("id")
        if not isinstance(task_id, str):
            raise FluxApiError("BFL Flux missing task id in response", detail=data)
        return task_id

    def _poll_and_download(self, task_id: str) -> bytes:
        url = f"{self._base()}/v1/get_result"
        max_polls = self._s.flux_api_max_polls
        interval = self._s.flux_api_poll_interval_s

        for attempt in range(max_polls):
            try:
                with httpx.Client(timeout=15.0) as client:
                    r = client.get(
                        url, headers=self._headers, params={"id": task_id}
                    )
            except httpx.TimeoutException as e:
                raise FluxApiError(f"BFL Flux poll timeout: {e}") from e
            except httpx.RequestError as e:
                raise FluxApiError(f"BFL Flux poll connection error: {e}") from e

            if r.status_code >= 400:
                raise FluxApiError(
                    f"BFL Flux poll HTTP {r.status_code}", detail=r.text[:2000]
                )

            data = r.json()
            status = data.get("status", "")

            if status in _TERMINAL_STATUSES:
                if status != _SUCCESS_STATUS:
                    raise FluxApiError(
                        f"BFL Flux task {task_id!r} ended with status {status!r}",
                        detail=data,
                    )
                result = data.get("result") or {}
                sample_url = result.get("sample")
                if not isinstance(sample_url, str):
                    raise FluxApiError(
                        "BFL Flux result missing 'sample' URL", detail=data
                    )
                log.info("flux_bfl_ready", task_id=task_id, attempts=attempt + 1)
                return self._download(sample_url)

            log.debug(
                "flux_bfl_polling",
                task_id=task_id,
                status=status,
                attempt=attempt + 1,
            )
            time.sleep(interval)

        raise FluxApiError(
            f"BFL Flux task {task_id!r} did not complete within "
            f"{max_polls * interval:.0f}s ({max_polls} polls × {interval}s)"
        )

    @staticmethod
    def _download(url: str) -> bytes:
        try:
            with httpx.Client(timeout=60.0, follow_redirects=True) as client:
                r = client.get(url)
        except httpx.TimeoutException as e:
            raise FluxApiError(f"BFL image download timeout: {e}") from e
        except httpx.RequestError as e:
            raise FluxApiError(f"BFL image download connection error: {e}") from e
        if r.status_code >= 400:
            raise FluxApiError(
                f"BFL image download HTTP {r.status_code}", detail=r.text[:200]
            )
        return r.content


def _together_is_flux2_model(model: str) -> bool:
    return "flux.2" in model.lower()


def _together_clamp_dims_flux2(width: int, height: int) -> tuple[int, int]:
    """FLUX.2 on Together: width/height must be 256–1920 and multiples of 8."""
    w = max(256, min(1920, int(width)))
    h = max(256, min(1920, int(height)))
    w = (w // 8) * 8
    h = (h // 8) * 8
    return w, h


class TogetherFluxImageClient:
    """Together AI images API — FLUX.2 [pro] and other image models.

    Docs: https://docs.together.ai/docs/quickstart-flux
    Endpoint: POST /v1/images/generations
    """

    def __init__(self, settings: Settings) -> None:
        if not settings.flux_api_key:
            raise FluxApiError(
                "flux_api_key is not set. Add SHORTS_FLUX_API_KEY (your Together API key) "
                "when SHORTS_FLUX_API_PROVIDER=together."
            )
        self._s = settings
        self._headers = {
            "Authorization": f"Bearer {settings.flux_api_key}",
            "Content-Type": "application/json",
        }

    def _build_body(self, prompt: str) -> dict[str, Any]:
        model = self._s.flux_api_model
        w, h = self._s.flux_api_width, self._s.flux_api_height
        if _together_is_flux2_model(model):
            w, h = _together_clamp_dims_flux2(w, h)
        else:
            w = max(64, (int(w) // 8) * 8)
            h = max(64, (int(h) // 8) * 8)

        body: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "width": w,
            "height": h,
        }
        if _together_is_flux2_model(model):
            # FLUX.2 on Together: use response_format=url (FLUX.2 native, no conflict).
            # Sending both response_format=base64 AND output_format=png causes HTTP 400.
            body["response_format"] = "url"
        else:
            # FLUX.1 / older models: get bytes inline (avoids extra HTTP round-trip)
            body["response_format"] = "base64"
            body["n"] = 1
        if self._s.flux_together_disable_safety_checker:
            body["disable_safety_checker"] = True
        return body

    def _post(self, body: dict[str, Any]) -> httpx.Response:
        url = f"{self._s.together_api_base_url.rstrip('/')}/v1/images/generations"
        try:
            with httpx.Client(timeout=self._s.flux_together_timeout_s) as client:
                return client.post(url, headers=self._headers, json=body)
        except httpx.TimeoutException as e:
            raise FluxApiError(f"Together image API timeout: {e}") from e
        except httpx.RequestError as e:
            raise FluxApiError(f"Together image API connection error: {e}") from e

    @staticmethod
    def _decode_response(r: httpx.Response, prompt_preview: str) -> bytes:
        data = r.json()
        err = data.get("error")
        if err is not None:
            raise FluxApiError(
                "Together image API returned error",
                detail=err if isinstance(err, (dict, str)) else data,
            )

        items = data.get("data")
        if not isinstance(items, list) or not items:
            raise FluxApiError("Together image API missing data[]", detail=data)

        first = items[0]
        if not isinstance(first, dict):
            raise FluxApiError("Together image API invalid data[0]", detail=data)

        b64 = first.get("b64_json")
        if isinstance(b64, str) and b64:
            try:
                raw = base64.b64decode(b64)
            except Exception as e:
                raise FluxApiError("Together image API invalid base64", detail=str(e)) from e
            log.info("flux_together_done", prompt_preview=prompt_preview[:80], bytes=len(raw))
            return raw

        img_url = first.get("url")
        if isinstance(img_url, str) and img_url:
            return BflFluxApiClient._download(img_url)

        raise FluxApiError(
            "Together image API response has no b64_json or url",
            detail=first,
        )

    def generate(self, prompt: str) -> bytes:
        max_extra = max(0, self._s.flux_together_http_retries)
        attempts: list[tuple[str, dict[str, Any]]] = [
            ("default", self._build_body(prompt)),
        ]
        if len(prompt) > 3200:
            attempts.append(
                ("truncated_3200", self._build_body(prompt[:3200].rstrip() + " …")),
            )

        last_status = 0
        last_text = ""
        for label, body in attempts:
            for retry_idx in range(max_extra + 1):
                r = self._post(body)
                last_status = r.status_code
                last_text = r.text

                if r.status_code < 400:
                    return self._decode_response(r, prompt)

                # Parse JSON for a clean message; fall back to raw text.
                try:
                    err_json = r.json()
                    err_msg = (
                        err_json.get("error", {}).get("message")
                        or err_json.get("message")
                        or r.text[:2000]
                    )
                except Exception:
                    err_msg = r.text[:2000]
                log.warning(
                    "together_image_http_error",
                    label=label,
                    retry=retry_idx,
                    status=r.status_code,
                    error_message=err_msg,
                    body_sent={k: v for k, v in body.items() if k != "prompt"},
                )

                # Retry same payload on transient / overloaded responses
                if r.status_code in (408, 429, 500, 502, 503, 504) and retry_idx < max_extra:
                    time.sleep(min(8.0, 1.5 * (2**retry_idx)))
                    continue

                # One backoff + retry for 400/422 (Together sometimes returns generic 400)
                if r.status_code in (400, 422) and retry_idx < max_extra:
                    time.sleep(2.0 + retry_idx * 2.0)
                    continue

                break

        raise FluxApiError(
            f"Together image API HTTP {last_status}",
            detail=last_text[:4000],
        )


def build_flux_api_client(settings: Settings) -> FluxImageApi:
    """Return BFL or Together client based on SHORTS_FLUX_API_PROVIDER."""
    provider = settings.flux_api_provider.lower().strip()
    if provider in ("bfl", "black-forest", "blackforest"):
        return BflFluxApiClient(settings)
    if provider in ("together", "together_ai", "together-ai"):
        return TogetherFluxImageClient(settings)
    raise FluxApiError(
        f"Unknown flux_api_provider {provider!r}. Use 'bfl' or 'together'."
    )
