"""DeepSeek publisher client — OpenAI-compatible HTTP, JSON mode.

Symmetric with `planner.deepseek_client.DeepSeekPlannerClient`: same retry
policy, same JSON repair pipeline, same correction loop. Used as the final
fallback tier when every Gemini model is unavailable.
"""

from __future__ import annotations

import json
import random
import time
from typing import Any

import httpx

from shorts_pipeline.config.settings import Settings
from shorts_pipeline.logging_setup import get_logger
from shorts_pipeline.planner.schema import NarrationPlan
from shorts_pipeline.publisher.json_helpers import build_correction_message, extract_json
from shorts_pipeline.publisher.prompts import SYSTEM_PROMPT, user_prompt
from shorts_pipeline.publisher.schema import PublishingPackage

log = get_logger(__name__)

_MAX_RETRIES = 4
_BACKOFF_BASE_S = 3.0
_BACKOFF_MAX_S = 30.0


class DeepSeekPublisherError(Exception):
    def __init__(
        self, message: str, *, status_code: int | None = None, detail: Any = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail


def _is_transient_deepseek_error(exc: DeepSeekPublisherError) -> bool:
    code = exc.status_code
    if code is None:
        return False
    return 500 <= code < 600 and code not in (400, 401, 403, 404)


class DeepSeekPublisherClient:
    """Generate a PublishingPackage via DeepSeek-V3 / R1."""

    def __init__(self, settings: Settings) -> None:
        if not settings.deepseek_api_key:
            raise DeepSeekPublisherError(
                "deepseek_api_key is not set. Set SHORTS_DEEPSEEK_API_KEY in .env "
                "or use planner_backend other than 'hybrid'."
            )
        self._settings = settings

    def _post_once(self, payload: dict[str, Any]) -> str:
        base = self._settings.deepseek_base_url.rstrip("/")
        url = f"{base}/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._settings.deepseek_api_key}",
            "Content-Type": "application/json",
        }
        try:
            with httpx.Client(timeout=self._settings.deepseek_timeout_s) as client:
                r = client.post(url, headers=headers, json=payload)
        except httpx.TimeoutException as e:
            raise DeepSeekPublisherError(f"DeepSeek timeout: {e}") from e
        except httpx.RequestError as e:
            raise DeepSeekPublisherError(f"DeepSeek connection error: {e}") from e

        if r.status_code >= 400:
            raise DeepSeekPublisherError(
                f"DeepSeek HTTP {r.status_code}",
                status_code=r.status_code,
                detail=r.text[:2000],
            )

        try:
            data = r.json()
        except json.JSONDecodeError as e:
            raise DeepSeekPublisherError(
                "DeepSeek returned non-JSON body", detail=r.text[:2000]
            ) from e

        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise DeepSeekPublisherError("DeepSeek missing choices[]", detail=data)
        msg = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise DeepSeekPublisherError("DeepSeek missing message.content", detail=data)
        return content

    def _post(self, payload: dict[str, Any]) -> str:
        max_retries = getattr(self._settings, "deepseek_transient_retries", 3)
        for attempt in range(1, max_retries + 1):
            try:
                return self._post_once(payload)
            except DeepSeekPublisherError as exc:
                if _is_transient_deepseek_error(exc) and attempt < max_retries:
                    delay = min(
                        _BACKOFF_MAX_S,
                        _BACKOFF_BASE_S * (2 ** (attempt - 1)),
                    ) + random.uniform(0.0, 1.0)
                    log.warning(
                        "publisher_deepseek_transient_retry",
                        attempt=attempt,
                        max_attempts=max_retries,
                        status_code=exc.status_code,
                        sleep_seconds=round(delay, 2),
                    )
                    time.sleep(delay)
                    continue
                raise

        raise DeepSeekPublisherError(
            "publisher_deepseek_post: retry loop exhausted (should not happen)"
        )

    def generate_package(
        self,
        figure_name: str,
        plan: NarrationPlan,
    ) -> PublishingPackage:
        base_payload: dict[str, Any] = {
            "model": self._settings.deepseek_model,
            "temperature": 0.9,
            "top_p": 0.95,
            "max_tokens": 4096,
            "response_format": {"type": "json_object"},
        }

        messages: list[dict[str, str]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt(figure_name, plan)},
        ]

        log.info(
            "publisher_deepseek_start",
            figure=figure_name,
            model=self._settings.deepseek_model,
            switch_context="[SWITCH] DeepSeek active for publisher — Gemini unavailable",
        )

        last_err: Exception | None = None
        last_obj: dict[str, Any] = {}

        for attempt in range(1, _MAX_RETRIES + 1):
            content = self._post({**base_payload, "messages": messages})

            try:
                obj = extract_json(content)
            except json.JSONDecodeError as e:
                last_err = e
                if attempt < _MAX_RETRIES:
                    log.warning(
                        "publisher_deepseek_json_parse_error_retry",
                        attempt=attempt,
                        error=str(e)[:200],
                    )
                    messages = [
                        *messages,
                        {"role": "assistant", "content": content},
                        {
                            "role": "user",
                            "content": (
                                "Your previous response contained invalid JSON "
                                f"(parse error: {e}). "
                                "Output ONLY a single valid JSON object — "
                                "no prose, no markdown fences, no trailing commas, "
                                "no comments, no truncation."
                            ),
                        },
                    ]
                    continue
                raise DeepSeekPublisherError(
                    "DeepSeek did not return parseable JSON", detail=content[:4000]
                ) from e

            try:
                pkg = PublishingPackage.model_validate(obj)
                log.info(
                    "publisher_deepseek_success",
                    figure=figure_name,
                    model=self._settings.deepseek_model,
                    attempt=attempt,
                )
                return pkg
            except Exception as err:
                last_err = err
                last_obj = obj
                if attempt < _MAX_RETRIES:
                    messages = [
                        *messages,
                        {"role": "assistant", "content": content},
                        {"role": "user", "content": build_correction_message(obj, err)},
                    ]

        raise DeepSeekPublisherError(
            f"Publishing-package validation failed after {_MAX_RETRIES} attempts: {last_err}",
            detail=last_obj,
        ) from last_err
