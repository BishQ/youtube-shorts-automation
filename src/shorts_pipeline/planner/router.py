"""Planner router — vLLM only.

This pipeline runs entirely on a local vLLM OpenAI-compatible server
(default Qwen/Qwen3-32B on http://127.0.0.1:8000/v1).
No cloud fallback, no API keys, no quota tracking. Valid
SHORTS_PLANNER_BACKEND values are 'vllm' (or aliases 'local'/'auto').
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from shorts_pipeline.config.settings import Settings
from shorts_pipeline.planner.schema import NarrationPlan
from shorts_pipeline.planner.vllm_client import VllmPlannerClient

_VALID_BACKENDS = frozenset({"vllm", "local", "auto"})


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
    """Return the vLLM planner client."""
    backend = (settings.planner_backend or "vllm").lower().strip()
    if backend not in _VALID_BACKENDS:
        raise ValueError(
            f"planner_backend={backend!r} is not supported. "
            "Only local vLLM is available — set SHORTS_PLANNER_BACKEND=vllm."
        )
    return VllmPlannerClient(settings)
