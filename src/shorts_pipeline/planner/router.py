"""Planner router — vLLM or Ollama (both OpenAI-compatible /v1)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from shorts_pipeline.config.settings import Settings
from shorts_pipeline.logging_setup import get_logger
from shorts_pipeline.planner.ollama_client import OllamaPlannerClient
from shorts_pipeline.planner.schema import NarrationPlan
from shorts_pipeline.planner.vllm_client import VllmPlannerClient

log = get_logger(__name__)

_VALID_BACKENDS = frozenset({"vllm", "ollama", "local", "auto"})


@runtime_checkable
class PlannerClient(Protocol):
    def generate_plan(
        self,
        figure_name: str,
        *,
        topic_type: str,
        language: str,
        user_feedback: str | None = None,
    ) -> NarrationPlan: ...


def build_planner_client(settings: Settings) -> PlannerClient:
    backend = (settings.planner_backend or "vllm").lower().strip()
    if backend not in _VALID_BACKENDS:
        raise ValueError(
            f"planner_backend={backend!r} is not supported. "
            "Use SHORTS_PLANNER_BACKEND=vllm or ollama."
        )
    if backend == "ollama":
        log.info(
            "planner_backend_ollama",
            model=settings.local_llm_model,
            base_url=settings.local_llm_base_url,
        )
        return OllamaPlannerClient(settings)
    log.info(
        "planner_backend_vllm",
        model=settings.local_llm_model,
        base_url=settings.local_llm_base_url,
    )
    return VllmPlannerClient(settings)
