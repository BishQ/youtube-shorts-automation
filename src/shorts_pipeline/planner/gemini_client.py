"""Gemini planner client — google-genai SDK, JSON mode, multi-turn validation retries."""

from __future__ import annotations

import json
import random
import time
from typing import Any

from shorts_pipeline.config.settings import Settings
from shorts_pipeline.planner.client import (
    _build_correction_message,
    _extract_json,
)
from shorts_pipeline.planner.word_budget import maybe_clamp_plan_json
from shorts_pipeline.planner.prompts import SYSTEM_PROMPT, user_prompt
from shorts_pipeline.planner.schema import NarrationPlan, validate_english_figure_v1
from shorts_pipeline.logging_setup import get_logger
from shorts_pipeline.planner.gemini_usage import record_generate_content_response
from shorts_pipeline.planner.wiki_grounding import fetch_grounding, format_for_prompt

log = get_logger(__name__)

_MAX_RETRIES = 5

# Transient HTTP / capacity errors (503 UNAVAILABLE, overload, etc.)
_MAX_TRANSIENT_HTTP_RETRIES = 6
_BACKOFF_BASE_S = 2.5
_BACKOFF_MAX_S = 48.0


def _is_transient_gemini_http_error(exc: BaseException | None) -> bool:
    """True when retrying the same request may succeed (server/load issues)."""
    if exc is None:
        return False

    def _check_one(e: BaseException) -> bool:
        code = getattr(e, "code", None)
        # Omit 429: router + model chain handle quota / RPM; local retry would delay fallback.
        if isinstance(code, int) and code in (408, 500, 502, 503, 504):
            return True
        status = getattr(e, "status", None)
        if isinstance(status, str) and status.upper() in (
            "UNAVAILABLE",
            "DEADLINE_EXCEEDED",
        ):
            return True
        blob = f"{e} {code} {status}".lower()
        return any(
            phrase in blob
            for phrase in (
                "503",
                "502",
                "504",
                "unavailable",
                "high demand",
                "try again later",
                "overloaded",
                "deadline exceeded",
                "temporarily",
            )
        )

    cur: BaseException | None = exc
    seen: set[int] = set()
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if _check_one(cur):
            return True
        cur = cur.__cause__  # type: ignore[assignment]
    return False


class GeminiPlannerError(Exception):
    def __init__(self, message: str, *, detail: Any = None) -> None:
        super().__init__(message)
        self.detail = detail


class GeminiPlannerClient:
    def __init__(
        self,
        settings: Settings,
        *,
        model: str | None = None,
        max_transient_http_retries: int = _MAX_TRANSIENT_HTTP_RETRIES,
    ) -> None:
        """
        Args:
            max_transient_http_retries: How many times to retry a single 503/502/504
                before re-raising.  Pass a lower value (e.g. 2) in hybrid mode so the
                router can switch to the next model or DeepSeek quickly instead of
                waiting through a long internal backoff cycle.
        """
        if not settings.gemini_api_key:
            raise GeminiPlannerError(
                "gemini_api_key is not set. Set SHORTS_GEMINI_API_KEY in .env "
                "(required for planner_backend=gemini, auto, or hybrid)."
            )
        try:
            from google import genai
        except ImportError as e:
            raise GeminiPlannerError(
                "google-genai SDK is not installed. Run: pip install google-genai"
            ) from e

        ordered = settings.gemini_models_ordered()
        if not ordered:
            raise GeminiPlannerError(
                "No Gemini model configured. Set SHORTS_GEMINI_MODEL or "
                "SHORTS_GEMINI_MODEL_CHAIN in .env."
            )
        self._model = (model or ordered[0]).strip()
        if not self._model:
            raise GeminiPlannerError("Gemini model id is empty")

        self._settings = settings
        self._genai = genai
        self._client = genai.Client(api_key=settings.gemini_api_key)
        self._max_transient_retries = max_transient_http_retries

    def _generate(self, system_text: str, history: list[dict]) -> str:
        """Send one chat turn.  history is a list of {role, parts} dicts in
        the format the genai SDK accepts.  Returns raw model text."""
        from google.genai import types

        config = types.GenerateContentConfig(
            system_instruction=system_text,
            response_mime_type="application/json",
            temperature=0.85,
            top_p=0.95,
            max_output_tokens=16000,
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
                if _is_transient_gemini_http_error(e) and attempt < self._max_transient_retries:
                    delay = min(
                        _BACKOFF_MAX_S,
                        _BACKOFF_BASE_S * (2 ** (attempt - 1)),
                    ) + random.uniform(0.0, 1.25)
                    log.warning(
                        "gemini_transient_retry",
                        model=self._model,
                        attempt=attempt,
                        max_attempts=_MAX_TRANSIENT_HTTP_RETRIES,
                        sleep_seconds=round(delay, 2),
                        error=str(e)[:300],
                    )
                    time.sleep(delay)
                    continue
                raise GeminiPlannerError(f"Gemini request failed: {e}") from e

            try:
                record_generate_content_response(resp)
            except Exception:
                pass

            text = (resp.text or "").strip()
            if not text:
                raise GeminiPlannerError(
                    "Gemini returned empty response", detail=str(resp)[:2000]
                )
            return text

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

        use_figure_name = getattr(self._settings, "image_prompts_include_figure_name", False)
        _validate_ctx = {"allow_figure_name": use_figure_name}

        # Gemini chat history: each turn = {"role": "user"|"model", "parts": [...]}
        history: list[dict] = [
            {"role": "user", "parts": [{"text": user_prompt(figure_name, use_figure_name=use_figure_name)}]},
        ]

        # Inject user feedback as a clarifying turn before generation
        if user_feedback and user_feedback.strip():
            history = [
                *history,
                {"role": "model", "parts": [{"text": "Understood. I will write the narration plan now."}]},
                {
                    "role": "user",
                    "parts": [{"text": (
                        "IMPORTANT — Before finalising the JSON, incorporate this feedback "
                        "from the producer:\n\n"
                        + user_feedback.strip()
                        + "\n\nNow produce the complete plan JSON as instructed."
                    )}],
                },
            ]

        last_err: Exception | None = None
        last_obj: dict = {}

        for attempt in range(1, _MAX_RETRIES + 1):
            text = self._generate(system_with_facts, history)

            try:
                obj = _extract_json(text)
                maybe_clamp_plan_json(obj)
            except json.JSONDecodeError as e:
                last_err = e
                # Retry with an explicit request for valid JSON — don't fail immediately.
                if attempt < _MAX_RETRIES:
                    log.warning(
                        "gemini_json_parse_error_retry",
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
                raise GeminiPlannerError(
                    "Gemini did not return parseable JSON", detail=text[:4000]
                ) from e

            try:
                plan = NarrationPlan.model_validate(obj, context=_validate_ctx)
                validate_english_figure_v1(plan, topic_type=topic_type, language=language)
                return plan
            except Exception as err:
                last_err = err
                last_obj = obj
                if attempt < _MAX_RETRIES:
                    history = [
                        *history,
                        {"role": "model", "parts": [{"text": text}]},
                        {"role": "user", "parts": [{"text": _build_correction_message(obj, err)}]},
                    ]

        raise GeminiPlannerError(
            f"Plan validation failed after {_MAX_RETRIES} attempts: {last_err}",
            detail=last_obj,
        ) from last_err
