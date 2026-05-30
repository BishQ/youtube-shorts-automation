"""vLLM planner client — local OpenAI-compatible HTTP API.

Talks to a locally hosted vLLM server (default http://127.0.0.1:8000/v1)
using the OpenAI /chat/completions schema. No API key, no quotas,
no cloud failover — this is the single planner backend for the pipeline.

Default model: settings.local_llm_model (must match vLLM --served-model-name).

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
from shorts_pipeline.planner.niches_compact import make_system_prompt, make_user_prompt
from shorts_pipeline.planner.niche_resolve import resolve_niche
from shorts_pipeline.planner.schema import NarrationPlan, validate_english_figure_v1
from shorts_pipeline.planner.structured_output import (
    apply_structured_output,
    json_schema_for,
    looks_like_structured_output_rejection,
    structured_output_fallback_modes,
)
from shorts_pipeline.planner.word_budget import maybe_clamp_plan_json, maybe_expand_plan_json
from shorts_pipeline.planner.niche_caps import caps_for
from shorts_pipeline.planner.grounding import fetch_multi_grounding, format_multi_for_prompt

log = get_logger(__name__)

_MAX_RETRIES = 8
_BACKOFF_BASE_S = 3.0
_BACKOFF_MAX_S = 30.0
# Local runtimes (LM Studio / vLLM) can crash on long structured generations.
# Keep this conservative; retries + correction loop handle the rest.
_MAX_GENERATION_TOKENS = 2500


class VllmPlannerError(Exception):
    def __init__(
        self, message: str, *, status_code: int | None = None, detail: Any = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail


def _is_transient_vllm_error(exc: VllmPlannerError) -> bool:
    code = exc.status_code
    if code is None:
        return True
    # Some OpenAI-compatible servers return HTTP 400 when the model process
    # crashes (LM Studio can do this). Treat as transient so we can retry.
    if code == 400 and "model has crashed" in str(exc.detail).lower():
        return True
    return 500 <= code < 600 and code not in (400, 401, 403, 404)


class VllmPlannerClient:
    """Generate a NarrationPlan via local vLLM (OpenAI-compatible /v1)."""

    def __init__(self, settings: Settings) -> None:
        if not settings.local_llm_model:
            raise VllmPlannerError(
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
        last_exc: VllmPlannerError | None = None
        for mode in modes:
            try:
                if mode is None:
                    return self._post(dict(payload))
                structured = apply_structured_output(
                    payload,
                    mode=mode,
                    schema=schema,
                    name="NarrationPlan",
                )
            except ValueError as exc:
                raise VllmPlannerError(str(exc)) from exc
            try:
                return self._post(structured)
            except VllmPlannerError as exc:
                if exc.status_code == 400 and looks_like_structured_output_rejection(exc.detail):
                    log.warning(
                        "vllm_structured_output_rejected",
                        mode=mode,
                        detail=str(exc.detail)[:300],
                    )
                    last_exc = exc
                    continue
                raise
        if last_exc is not None:
            raise last_exc
        raise VllmPlannerError("structured output: no modes to try")

    def _post_once(self, payload: dict[str, Any]) -> str:
        base = self._settings.local_llm_base_url.rstrip("/")
        url = f"{base}/chat/completions"
        headers = {"Content-Type": "application/json"}
        try:
            with httpx.Client(timeout=self._settings.local_llm_timeout_s) as client:
                r = client.post(url, headers=headers, json=payload)
        except httpx.TimeoutException as e:
            raise VllmPlannerError(f"vLLM timeout: {e}") from e
        except httpx.RequestError as e:
            raise VllmPlannerError(
                f"vLLM connection error at {base}: {e}. "
                "Is vLLM running? Start it: bash scripts/start_vllm.sh"
            ) from e

        if r.status_code >= 400:
            detail = r.text[:2000]
            raise VllmPlannerError(
                f"vLLM HTTP {r.status_code}: {detail}",
                status_code=r.status_code,
                detail=detail,
            )

        try:
            data = r.json()
        except json.JSONDecodeError as e:
            raise VllmPlannerError(
                "vLLM returned non-JSON body", detail=r.text[:2000]
            ) from e

        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise VllmPlannerError("vLLM missing choices[]", detail=data)
        msg = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise VllmPlannerError("vLLM missing message.content", detail=data)
        return content

    def _post(self, payload: dict[str, Any]) -> str:
        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                return self._post_once(payload)
            except VllmPlannerError as exc:
                if _is_transient_vllm_error(exc) and attempt < max_retries:
                    delay = min(
                        _BACKOFF_MAX_S,
                        _BACKOFF_BASE_S * (2 ** (attempt - 1)),
                    ) + random.uniform(0.0, 1.0)
                    log.warning(
                        "vllm_transient_retry",
                        attempt=attempt,
                        max_attempts=max_retries,
                        status_code=exc.status_code,
                        sleep_seconds=round(delay, 2),
                    )
                    time.sleep(delay)
                    continue
                raise
        raise VllmPlannerError("vllm_post: retry loop exhausted (should not happen)")

    def generate_plan(
        self,
        figure_name: str,
        *,
        topic_type: str,
        language: str,
        user_feedback: str | None = None,
    ) -> NarrationPlan:
        niche = resolve_niche(topic_type)
        grounding = fetch_multi_grounding(figure_name)
        facts_block = format_multi_for_prompt(grounding)
        system_with_facts = make_system_prompt(niche) + (
            "\n\n" + facts_block[:8000] if facts_block else ""
        )
        if not grounding.found_any:
            import warnings

            warnings.warn(
                f"grounding_missing: topic={figure_name!r} — proceeding without verified facts — hallucination risk is higher",
                stacklevel=2,
            )

        base_payload: dict[str, Any] = {
            "model": self._settings.local_llm_model,
            "temperature": min(
                self._settings.local_llm_temperature,
                self._settings.local_llm_planner_temperature,
            ),
            "top_p": 0.9,
            "max_tokens": min(self._settings.local_llm_max_tokens, _MAX_GENERATION_TOKENS),
        }

        use_figure_name = getattr(self._settings, "image_prompts_include_figure_name", False)
        # Validation gates (word budgets, etc.) depend on ValidationInfo.context["niche"].
        # If we don't pass it, plans get validated against conservative defaults and
        # niches like "crime" will fail the word cap.
        _validate_ctx = {
            "allow_figure_name": use_figure_name,
            "niche": niche,
        }
        _min_words, _max_words, _max_syl = caps_for(niche)

        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_with_facts},
            {
                "role": "user",
                "content": make_user_prompt(
                    niche, figure_name, use_figure_name=use_figure_name
                ),
            },
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
        initial_messages = list(messages)

        log.info(
            "vllm_generate_start",
            figure=figure_name,
            niche=niche,
            model=self._settings.local_llm_model,
            base_url=self._settings.local_llm_base_url,
        )

        last_err: Exception | None = None
        last_obj: dict[str, Any] = {}
        schema = json_schema_for(NarrationPlan, name="NarrationPlan")
        structured_mode_override: str | None = None

        for attempt in range(1, _MAX_RETRIES + 1):
            content = self._post_structured(
                {**base_payload, "messages": messages},
                schema=schema,
                mode_override=structured_mode_override,
            )

            try:
                obj = _extract_json(content)
                # Deterministic small trims/pads to help convergence inside the niche caps.
                maybe_clamp_plan_json(obj, max_words=_max_words)
                maybe_expand_plan_json(obj, min_words=_min_words)
                # Hard policy: only 2 question marks in the entire output:
                # clause 1 hook + end_plate_question. Strip stray '?' deterministically.
                if isinstance(obj, dict):
                    clauses = obj.get("clauses")
                    if isinstance(clauses, list) and clauses:
                        for i, c in enumerate(clauses):
                            if i == 0:
                                continue
                            if isinstance(c, dict) and isinstance(c.get("text"), str):
                                c["text"] = c["text"].replace("?", "").strip()
                    if isinstance(obj.get("end_plate_question"), str):
                        q = obj["end_plate_question"].strip()
                        if not q.endswith("?"):
                            obj["end_plate_question"] = q + "?"
                # Persist niche on the object so downstream stages + saved plan.json
                # remain self-describing and caps can be derived without extra context.
                if isinstance(obj, dict) and not obj.get("niche"):
                    obj["niche"] = niche
            except json.JSONDecodeError as e:
                if attempt < _MAX_RETRIES:
                    log.warning(
                        "vllm_json_parse_error_retry",
                        attempt=attempt,
                        error=str(e)[:200],
                    )
                    structured_mode_override = None
                    messages = [
                        *initial_messages,
                        {
                            "role": "user",
                            "content": (
                                "Your previous response contained invalid JSON "
                                f"(parse error: {e}). "
                                "Output ONLY a single valid JSON object — "
                                "no prose, no markdown fences, no trailing commas, "
                                "no comments, no truncation. Start with { and end with }."
                            ),
                        },
                    ]
                    last_err = e
                    continue
                raise VllmPlannerError(
                    "vLLM did not return parseable JSON", detail=content[:4000]
                ) from e

            try:
                plan = NarrationPlan.model_validate(obj, context=_validate_ctx)
                validate_english_figure_v1(plan, topic_type=topic_type, language=language)
                log.info(
                    "vllm_generate_success",
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
                        *initial_messages,
                        {"role": "assistant", "content": json.dumps(obj, ensure_ascii=False)[:1200]},
                        {"role": "user", "content": _build_correction_message(obj, err)},
                    ]

        raise VllmPlannerError(
            f"Plan validation failed after {_MAX_RETRIES} attempts: {last_err}",
            detail=last_obj,
        ) from last_err
