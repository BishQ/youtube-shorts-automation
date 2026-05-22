"""Ollama planner client — local OpenAI-compatible HTTP API.

Talks to a locally hosted Ollama server (default http://127.0.0.1:11434/v1)
using the same OpenAI /chat/completions schema. No API key, no quotas,
no cloud failover — this is the single planner backend for the pipeline.

Default model: `qwen3.6:27b` (settings.local_llm_model).

Mirrors the validation / correction loop that the old Gemini and DeepSeek
clients used: parse JSON, validate NarrationPlan, on failure feed the
correction message back as a new user turn and retry up to _MAX_RETRIES.
"""

from __future__ import annotations

import json
import random
import time
from typing import Any

import httpx

from shorts_pipeline.config.settings import Settings
from shorts_pipeline.logging_setup import get_logger
from shorts_pipeline.planner.client import _build_correction_message, _extract_json
from shorts_pipeline.planner.word_budget import maybe_clamp_plan_json
from shorts_pipeline.planner.prompts import SYSTEM_PROMPT, user_prompt
from shorts_pipeline.planner.schema import NarrationPlan, validate_english_figure_v1
from shorts_pipeline.planner.wiki_grounding import fetch_grounding, format_for_prompt

log = get_logger(__name__)

_MAX_RETRIES = 5
_BACKOFF_BASE_S = 3.0
_BACKOFF_MAX_S = 30.0


class OllamaPlannerError(Exception):
    def __init__(
        self, message: str, *, status_code: int | None = None, detail: Any = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail


def _is_transient_ollama_error(exc: OllamaPlannerError) -> bool:
    code = exc.status_code
    if code is None:
        # Network errors (timeout / connection refused) are transient
        return True
    return 500 <= code < 600 and code not in (400, 401, 403, 404)


class OllamaPlannerClient:
    """Generate a NarrationPlan via local Ollama (OpenAI-compatible /v1)."""

    def __init__(self, settings: Settings) -> None:
        if not settings.local_llm_model:
            raise OllamaPlannerError(
                "local_llm_model is not set. Set SHORTS_LOCAL_LLM_MODEL in .env "
                "(e.g. qwen3.6:27b)."
            )
        self._settings = settings

    def _post_once(self, payload: dict[str, Any]) -> str:
        base = self._settings.local_llm_base_url.rstrip("/")
        url = f"{base}/chat/completions"
        headers = {"Content-Type": "application/json"}
        try:
            with httpx.Client(timeout=self._settings.local_llm_timeout_s) as client:
                r = client.post(url, headers=headers, json=payload)
        except httpx.TimeoutException as e:
            raise OllamaPlannerError(f"Ollama timeout: {e}") from e
        except httpx.RequestError as e:
            raise OllamaPlannerError(f"Ollama connection error at {base}: {e}") from e

        if r.status_code >= 400:
            raise OllamaPlannerError(
                f"Ollama HTTP {r.status_code}",
                status_code=r.status_code,
                detail=r.text[:2000],
            )

        try:
            data = r.json()
        except json.JSONDecodeError as e:
            raise OllamaPlannerError(
                "Ollama returned non-JSON body", detail=r.text[:2000]
            ) from e

        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise OllamaPlannerError("Ollama missing choices[]", detail=data)
        msg = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise OllamaPlannerError("Ollama missing message.content", detail=data)
        return content

    def _post(self, payload: dict[str, Any]) -> str:
        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                return self._post_once(payload)
            except OllamaPlannerError as exc:
                if _is_transient_ollama_error(exc) and attempt < max_retries:
                    delay = min(
                        _BACKOFF_MAX_S,
                        _BACKOFF_BASE_S * (2 ** (attempt - 1)),
                    ) + random.uniform(0.0, 1.0)
                    log.warning(
                        "ollama_transient_retry",
                        attempt=attempt,
                        max_attempts=max_retries,
                        status_code=exc.status_code,
                        sleep_seconds=round(delay, 2),
                    )
                    time.sleep(delay)
                    continue
                raise
        raise OllamaPlannerError("ollama_post: retry loop exhausted (should not happen)")

    def generate_plan(
        self,
        figure_name: str,
        *,
        topic_type: str,
        language: str,
        user_feedback: str | None = None,
    ) -> NarrationPlan:
        grounding = fetch_grounding(figure_name)
        wiki_block = format_for_prompt(grounding)
        system_with_facts = SYSTEM_PROMPT + ("\n\n" + wiki_block if wiki_block else "")
        if not grounding.found:
            import warnings

            warnings.warn(
                f"wiki_grounding_missing: figure={figure_name!r} — "
                "proceeding without Wikipedia facts — hallucination risk is higher",
                stacklevel=2,
            )

        base_payload: dict[str, Any] = {
            "model": self._settings.local_llm_model,
            "temperature": self._settings.local_llm_temperature,
            "top_p": 0.95,
            "max_tokens": self._settings.local_llm_max_tokens,
            # Ollama OpenAI-compat supports JSON mode via this field.
            "response_format": {"type": "json_object"},
        }

        use_figure_name = getattr(self._settings, "image_prompts_include_figure_name", False)
        _validate_ctx = {"allow_figure_name": use_figure_name}

        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_with_facts},
            {"role": "user", "content": user_prompt(figure_name, use_figure_name=use_figure_name)},
        ]

        if user_feedback and user_feedback.strip():
            messages = [
                *messages,
                {"role": "assistant", "content": "Understood. I will write the narration plan now."},
                {
                    "role": "user",
                    "content": (
                        "IMPORTANT — Before finalising the JSON, incorporate this feedback "
                        "from the producer:\n\n"
                        + user_feedback.strip()
                        + "\n\nNow produce the complete plan JSON as instructed."
                    ),
                },
            ]

        log.info(
            "ollama_generate_start",
            figure=figure_name,
            model=self._settings.local_llm_model,
            base_url=self._settings.local_llm_base_url,
        )

        last_err: Exception | None = None
        last_obj: dict[str, Any] = {}

        for attempt in range(1, _MAX_RETRIES + 1):
            content = self._post({**base_payload, "messages": messages})

            try:
                obj = _extract_json(content)
                maybe_clamp_plan_json(obj)
            except json.JSONDecodeError as e:
                if attempt < _MAX_RETRIES:
                    log.warning(
                        "ollama_json_parse_error_retry",
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
                    last_err = e
                    continue
                raise OllamaPlannerError(
                    "Ollama did not return parseable JSON", detail=content[:4000]
                ) from e

            try:
                plan = NarrationPlan.model_validate(obj, context=_validate_ctx)
                validate_english_figure_v1(plan, topic_type=topic_type, language=language)
                log.info(
                    "ollama_generate_success",
                    figure=figure_name,
                    model=self._settings.local_llm_model,
                    attempt=attempt,
                )
                return plan
            except Exception as err:
                last_err = err
                last_obj = obj
                if attempt < _MAX_RETRIES:
                    messages = [
                        *messages,
                        {"role": "assistant", "content": content},
                        {"role": "user", "content": _build_correction_message(obj, err)},
                    ]

        raise OllamaPlannerError(
            f"Plan validation failed after {_MAX_RETRIES} attempts: {last_err}",
            detail=last_obj,
        ) from last_err
