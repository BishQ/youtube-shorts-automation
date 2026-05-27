"""vLLM publisher client — local OpenAI-compatible HTTP, JSON mode.

Generates the YouTube PublishingPackage (titles, description, tags,
thumbnail brief, CTR strategy) using the same local vLLM server the
planner uses. Default model: settings.local_llm_model.
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
from shorts_pipeline.planner.structured_output import (
    apply_structured_output,
    json_schema_for,
    looks_like_structured_output_rejection,
    structured_output_fallback_modes,
)
from shorts_pipeline.publisher.json_helpers import build_correction_message, extract_json
from shorts_pipeline.publisher.prompts import SYSTEM_PROMPT, user_prompt
from shorts_pipeline.publisher.schema import PublishingPackage

log = get_logger(__name__)

_MAX_RETRIES = 4
_BACKOFF_BASE_S = 3.0
_BACKOFF_MAX_S = 30.0
_MAX_RETRY_CONTEXT_CHARS = 1200


class VllmPublisherError(Exception):
    def __init__(
        self, message: str, *, status_code: int | None = None, detail: Any = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail


def _is_transient_vllm_error(exc: VllmPublisherError) -> bool:
    code = exc.status_code
    if code is None:
        return True
    return 500 <= code < 600 and code not in (400, 401, 403, 404)


class VllmPublisherClient:
    """Generate a PublishingPackage via local vLLM."""

    def __init__(self, settings: Settings) -> None:
        if not settings.local_llm_model:
            raise VllmPublisherError(
                "local_llm_model is not set. Set SHORTS_LOCAL_LLM_MODEL in .env "
                "(must match vLLM --served-model-name, e.g. Qwen/Qwen3-32B)."
            )
        self._settings = settings

    def _post_structured(
        self,
        payload: dict[str, Any],
        *,
        schema: dict[str, Any],
        mode_override: str | None = None,
    ) -> str:
        primary = mode_override or self._settings.local_llm_structured_output
        modes = structured_output_fallback_modes(primary)
        last_exc: VllmPublisherError | None = None
        for mode in modes:
            try:
                if mode is None:
                    return self._post(dict(payload))
                structured = apply_structured_output(
                    payload,
                    mode=mode,
                    schema=schema,
                    name="PublishingPackage",
                )
            except ValueError as exc:
                raise VllmPublisherError(str(exc)) from exc
            try:
                return self._post(structured)
            except VllmPublisherError as exc:
                if exc.status_code == 400 and looks_like_structured_output_rejection(exc.detail):
                    log.warning(
                        "publisher_vllm_structured_output_rejected",
                        mode=mode,
                        detail=str(exc.detail)[:300],
                    )
                    last_exc = exc
                    continue
                raise
        if last_exc is not None:
            raise last_exc
        raise VllmPublisherError("structured output: no modes to try")

    def _post_once(self, payload: dict[str, Any]) -> str:
        base = self._settings.local_llm_base_url.rstrip("/")
        url = f"{base}/chat/completions"
        headers = {"Content-Type": "application/json"}
        try:
            with httpx.Client(timeout=self._settings.local_llm_timeout_s) as client:
                r = client.post(url, headers=headers, json=payload)
        except httpx.TimeoutException as e:
            raise VllmPublisherError(f"vLLM timeout: {e}") from e
        except httpx.RequestError as e:
            raise VllmPublisherError(
                f"vLLM connection error at {base}: {e}. "
                "Is vLLM running? Start it: bash scripts/start_vllm.sh"
            ) from e

        if r.status_code >= 400:
            detail = r.text[:2000]
            raise VllmPublisherError(
                f"vLLM HTTP {r.status_code}: {detail}",
                status_code=r.status_code,
                detail=detail,
            )

        try:
            data = r.json()
        except json.JSONDecodeError as e:
            raise VllmPublisherError(
                "vLLM returned non-JSON body", detail=r.text[:2000]
            ) from e

        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise VllmPublisherError("vLLM missing choices[]", detail=data)
        msg = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise VllmPublisherError("vLLM missing message.content", detail=data)
        return content

    def _post(self, payload: dict[str, Any]) -> str:
        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                return self._post_once(payload)
            except VllmPublisherError as exc:
                if _is_transient_vllm_error(exc) and attempt < max_retries:
                    delay = min(
                        _BACKOFF_MAX_S,
                        _BACKOFF_BASE_S * (2 ** (attempt - 1)),
                    ) + random.uniform(0.0, 1.0)
                    log.warning(
                        "publisher_vllm_transient_retry",
                        attempt=attempt,
                        max_attempts=max_retries,
                        status_code=exc.status_code,
                        sleep_seconds=round(delay, 2),
                    )
                    time.sleep(delay)
                    continue
                raise

        raise VllmPublisherError(
            "publisher_vllm_post: retry loop exhausted (should not happen)"
        )

    def generate_package(
        self,
        figure_name: str,
        plan: NarrationPlan,
    ) -> PublishingPackage:
        base_payload: dict[str, Any] = {
            "model": self._settings.local_llm_model,
            "temperature": 0.9,
            "top_p": 0.95,
            "max_tokens": min(self._settings.local_llm_max_tokens, 4096),
        }

        messages: list[dict[str, str]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt(figure_name, plan)},
        ]

        log.info(
            "publisher_vllm_start",
            figure=figure_name,
            model=self._settings.local_llm_model,
        )

        last_err: Exception | None = None
        last_obj: dict[str, Any] = {}
        schema = json_schema_for(PublishingPackage, name="PublishingPackage")
        structured_mode_override: str | None = None

        for attempt in range(1, _MAX_RETRIES + 1):
            content = self._post_structured(
                {**base_payload, "messages": messages},
                schema=schema,
                mode_override=structured_mode_override,
            )

            try:
                obj = extract_json(content)
            except json.JSONDecodeError as e:
                last_err = e
                if attempt < _MAX_RETRIES:
                    log.warning(
                        "publisher_vllm_json_parse_error_retry",
                        attempt=attempt,
                        error=str(e)[:200],
                    )
                    structured_mode_override = "json_object"
                    messages = [
                        *messages,
                        {"role": "assistant", "content": content[:_MAX_RETRY_CONTEXT_CHARS]},
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
                raise VllmPublisherError(
                    "vLLM did not return parseable JSON", detail=content[:4000]
                ) from e

            try:
                pkg = PublishingPackage.model_validate(obj)
                log.info(
                    "publisher_vllm_success",
                    figure=figure_name,
                    model=self._settings.local_llm_model,
                    attempt=attempt,
                )
                return pkg
            except Exception as err:
                last_err = err
                last_obj = obj
                if attempt < _MAX_RETRIES:
                    messages = [
                        *messages,
                        {"role": "assistant", "content": content[:_MAX_RETRY_CONTEXT_CHARS]},
                        {"role": "user", "content": build_correction_message(obj, err)},
                    ]

        raise VllmPublisherError(
            f"Publishing-package validation failed after {_MAX_RETRIES} attempts: {last_err}",
            detail=last_obj,
        ) from last_err
