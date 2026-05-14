"""Gemini publisher client — google-genai SDK, JSON mode, validation retry loop.

Mirrors the behaviour of `planner.gemini_client.GeminiPlannerClient` so the
two surfaces stay symmetric (transient HTTP retry policy, multi-turn validator
correction loop, JSON repair). Keeping them parallel makes hybrid failover
predictable for the operator.
"""

from __future__ import annotations

import json
import random
import time
from typing import Any

from shorts_pipeline.config.settings import Settings
from shorts_pipeline.logging_setup import get_logger
from shorts_pipeline.planner.gemini_client import (
    _is_transient_gemini_http_error,
)
from shorts_pipeline.planner.gemini_usage import record_generate_content_response
from shorts_pipeline.planner.schema import NarrationPlan
from shorts_pipeline.publisher.json_helpers import build_correction_message, extract_json
from shorts_pipeline.publisher.prompts import SYSTEM_PROMPT, user_prompt
from shorts_pipeline.publisher.schema import PublishingPackage

log = get_logger(__name__)

_MAX_RETRIES = 4

_MAX_TRANSIENT_HTTP_RETRIES = 6
_BACKOFF_BASE_S = 2.5
_BACKOFF_MAX_S = 48.0


class GeminiPublisherError(Exception):
    def __init__(self, message: str, *, detail: Any = None) -> None:
        super().__init__(message)
        self.detail = detail


class GeminiPublisherClient:
    """Generate a PublishingPackage via Gemini, with bounded retries."""

    def __init__(
        self,
        settings: Settings,
        *,
        model: str | None = None,
        max_transient_http_retries: int = _MAX_TRANSIENT_HTTP_RETRIES,
    ) -> None:
        if not settings.gemini_api_key:
            raise GeminiPublisherError(
                "gemini_api_key is not set. Set SHORTS_GEMINI_API_KEY in .env "
                "(required for publisher_backend=gemini, auto, or hybrid)."
            )
        try:
            from google import genai
        except ImportError as e:
            raise GeminiPublisherError(
                "google-genai SDK is not installed. Run: pip install google-genai"
            ) from e

        ordered = settings.gemini_models_ordered()
        if not ordered:
            raise GeminiPublisherError(
                "No Gemini model configured. Set SHORTS_GEMINI_MODEL or "
                "SHORTS_GEMINI_MODEL_CHAIN in .env."
            )
        self._model = (model or ordered[0]).strip()
        if not self._model:
            raise GeminiPublisherError("Gemini model id is empty")

        self._settings = settings
        self._genai = genai
        self._client = genai.Client(api_key=settings.gemini_api_key)
        self._max_transient_retries = max_transient_http_retries

    def _generate(self, system_text: str, history: list[dict]) -> str:
        from google.genai import types

        config = types.GenerateContentConfig(
            system_instruction=system_text,
            response_mime_type="application/json",
            temperature=0.9,
            top_p=0.95,
            max_output_tokens=4096,
        )

        attempt = 0
        while True:
            attempt += 1
            try:
                resp = self._client.models.generate_content(
                    model=self._model,
                    contents=history,
                    config=config,
                )
            except Exception as e:
                if (
                    _is_transient_gemini_http_error(e)
                    and attempt < self._max_transient_retries
                ):
                    delay = min(
                        _BACKOFF_MAX_S,
                        _BACKOFF_BASE_S * (2 ** (attempt - 1)),
                    ) + random.uniform(0.0, 1.25)
                    log.warning(
                        "publisher_gemini_transient_retry",
                        model=self._model,
                        attempt=attempt,
                        max_attempts=self._max_transient_retries,
                        sleep_seconds=round(delay, 2),
                        error=str(e)[:300],
                    )
                    time.sleep(delay)
                    continue
                raise GeminiPublisherError(f"Gemini publisher request failed: {e}") from e

            try:
                record_generate_content_response(resp)
            except Exception:
                pass

            text = (resp.text or "").strip()
            if not text:
                raise GeminiPublisherError(
                    "Gemini returned empty response", detail=str(resp)[:2000]
                )
            return text

    def generate_package(
        self,
        figure_name: str,
        plan: NarrationPlan,
    ) -> PublishingPackage:
        history: list[dict] = [
            {"role": "user", "parts": [{"text": user_prompt(figure_name, plan)}]},
        ]

        last_err: Exception | None = None
        last_obj: dict[str, Any] = {}

        for attempt in range(1, _MAX_RETRIES + 1):
            text = self._generate(SYSTEM_PROMPT, history)

            try:
                obj = extract_json(text)
            except json.JSONDecodeError as e:
                last_err = e
                if attempt < _MAX_RETRIES:
                    log.warning(
                        "publisher_gemini_json_parse_error_retry",
                        attempt=attempt,
                        error=str(e)[:200],
                    )
                    history = [
                        *history,
                        {"role": "model", "parts": [{"text": text}]},
                        {
                            "role": "user",
                            "parts": [
                                {
                                    "text": (
                                        "Your previous response contained invalid JSON "
                                        f"(parse error: {e}). "
                                        "Output ONLY a single valid JSON object — "
                                        "no prose, no markdown fences, no trailing commas, "
                                        "no comments, no truncation."
                                    )
                                }
                            ],
                        },
                    ]
                    continue
                raise GeminiPublisherError(
                    "Gemini did not return parseable JSON", detail=text[:4000]
                ) from e

            try:
                pkg = PublishingPackage.model_validate(obj)
                log.info(
                    "publisher_gemini_success",
                    figure=figure_name,
                    model=self._model,
                    attempt=attempt,
                )
                return pkg
            except Exception as err:
                last_err = err
                last_obj = obj
                if attempt < _MAX_RETRIES:
                    history = [
                        *history,
                        {"role": "model", "parts": [{"text": text}]},
                        {
                            "role": "user",
                            "parts": [{"text": build_correction_message(obj, err)}],
                        },
                    ]

        raise GeminiPublisherError(
            f"Publishing-package validation failed after {_MAX_RETRIES} attempts: {last_err}",
            detail=last_obj,
        ) from last_err
