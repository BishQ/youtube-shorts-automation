"""Publisher router — Ollama only.

Mirrors planner/router.py: a single local backend, no failover.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from shorts_pipeline.config.settings import Settings
from shorts_pipeline.logging_setup import get_logger
from shorts_pipeline.planner.schema import NarrationPlan
from shorts_pipeline.publisher.ollama_client import OllamaPublisherClient
from shorts_pipeline.publisher.schema import PublishingPackage

log = get_logger(__name__)


@runtime_checkable
class PublisherClient(Protocol):
    def generate_package(
        self,
        figure_name: str,
        plan: NarrationPlan,
    ) -> PublishingPackage: ...


def build_publisher_client(settings: Settings) -> PublisherClient:
    """Return the Ollama publisher client."""
    backend = (settings.planner_backend or "ollama").lower().strip()
    if backend not in ("ollama", "local", "auto"):
        raise ValueError(
            f"planner_backend={backend!r} is no longer supported by the publisher. "
            "Only local Ollama is available — set SHORTS_PLANNER_BACKEND=ollama."
        )
    log.info(
        "publisher_backend_ollama",
        model=settings.local_llm_model,
        base_url=settings.local_llm_base_url,
    )
    return OllamaPublisherClient(settings)
