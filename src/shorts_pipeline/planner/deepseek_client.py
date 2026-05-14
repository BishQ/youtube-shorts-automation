"""DeepSeek planner client — OpenAI-compatible HTTP API, same plan flow as Gemini.

Used as the final-tier fallback in hybrid mode when all Gemini models are
unavailable (503 capacity overload or 429 quota exhaustion).
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


def _is_transient_deepseek_error(exc: DeepSeekPlannerError) -> bool:
    """5xx server errors from DeepSeek that may resolve on retry."""
    code = exc.status_code
    if code is None:
        return False
    return 500 <= code < 600 and code not in (400, 401, 403, 404)


class DeepSeekPlannerError(Exception):
    def __init__(
        self, message: str, *, status_code: int | None = None, detail: Any = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail


class DeepSeekPlannerClient:
    def __init__(self, settings: Settings) -> None:
        if not settings.deepseek_api_key:
            raise DeepSeekPlannerError(
                "deepseek_api_key is not set. Set SHORTS_DEEPSEEK_API_KEY in .env "
                "or use planner_backend other than 'hybrid'."
            )
        self._settings = settings

    def _post_once(self, payload: dict[str, Any]) -> str:
        """Single HTTP call — raises DeepSeekPlannerError on any failure."""
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
            raise DeepSeekPlannerError(f"DeepSeek timeout: {e}") from e
        except httpx.RequestError as e:
            raise DeepSeekPlannerError(f"DeepSeek connection error: {e}") from e

        if r.status_code >= 400:
            raise DeepSeekPlannerError(
                f"DeepSeek HTTP {r.status_code}",
                status_code=r.status_code,
                detail=r.text[:2000],
            )

        try:
            data = r.json()
        except json.JSONDecodeError as e:
            raise DeepSeekPlannerError(
                "DeepSeek returned non-JSON body", detail=r.text[:2000]
            ) from e

        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise DeepSeekPlannerError("DeepSeek missing choices[]", detail=data)
        msg = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise DeepSeekPlannerError("DeepSeek missing message.content", detail=data)
        return content

    def _post(self, payload: dict[str, Any]) -> str:
        """HTTP call with exponential backoff retry on transient 5xx errors."""
        max_retries = getattr(self._settings, "deepseek_transient_retries", 3)
        for attempt in range(1, max_retries + 1):
            try:
                return self._post_once(payload)
            except DeepSeekPlannerError as exc:
                if _is_transient_deepseek_error(exc) and attempt < max_retries:
                    delay = min(
                        _BACKOFF_MAX_S,
                        _BACKOFF_BASE_S * (2 ** (attempt - 1)),
                    ) + random.uniform(0.0, 1.0)
                    log.warning(
                        "deepseek_transient_retry",
                        attempt=attempt,
                        max_attempts=max_retries,
                        status_code=exc.status_code,
                        sleep_seconds=round(delay, 2),
                    )
                    time.sleep(delay)
                    continue
                raise

        raise DeepSeekPlannerError("deepseek_post: retry loop exhausted (should not happen)")

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
            "model": self._settings.deepseek_model,
            "temperature": 0.85,
            "top_p": 0.95,
            "max_tokens": 8192,
            "response_format": {"type": "json_object"},
        }

        use_figure_name = getattr(self._settings, "image_prompts_include_figure_name", False)
        _validate_ctx = {"allow_figure_name": use_figure_name}

        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_with_facts},
            {"role": "user", "content": user_prompt(figure_name, use_figure_name=use_figure_name)},
        ]

        # Inject user feedback as a clarifying turn before generation
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
            "deepseek_generate_start",
            figure=figure_name,
            model=self._settings.deepseek_model,
            switch_context="[SWITCH] DeepSeek active — Gemini unavailable",
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
                        "deepseek_json_parse_error_retry",
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
                raise DeepSeekPlannerError(
                    "DeepSeek did not return parseable JSON", detail=content[:4000]
                ) from e

            try:
                plan = NarrationPlan.model_validate(obj, context=_validate_ctx)
                validate_english_figure_v1(plan, topic_type=topic_type, language=language)
                log.info(
                    "deepseek_generate_success",
                    figure=figure_name,
                    model=self._settings.deepseek_model,
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

        raise DeepSeekPlannerError(
            f"Plan validation failed after {_MAX_RETRIES} attempts: {last_err}",
            detail=last_obj,
        ) from last_err
