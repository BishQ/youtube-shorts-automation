"""Planner router — Ollama only.

This pipeline runs entirely on a local Ollama server (default qwen3.6:27b).
No cloud fallback, no API keys, no quota tracking.  The only valid
SHORTS_PLANNER_BACKEND values are 'ollama' (or its aliases 'local'/'auto').
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from shorts_pipeline.config.settings import Settings
from shorts_pipeline.logging_setup import get_logger
from shorts_pipeline.planner.ollama_client import OllamaPlannerClient
from shorts_pipeline.planner.schema import NarrationPlan

log = get_logger(__name__)


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
    """Return the Ollama planner client.

    `settings.planner_backend` is accepted but only 'ollama' / 'local' /
    'auto' are valid — any other value raises so old configs surface.
    """
    backend = (settings.planner_backend or "ollama").lower().strip()
    if backend not in ("ollama", "local", "auto"):
        raise ValueError(
            f"planner_backend={backend!r} is no longer supported. "
            "Only local Ollama is available — set SHORTS_PLANNER_BACKEND=ollama."
        )
    log.info(
        "planner_backend_ollama",
        model=settings.local_llm_model,
        base_url=settings.local_llm_base_url,
    )
    return OllamaPlannerClient(settings)
