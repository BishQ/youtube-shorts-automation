"""Publisher router — vLLM only.

Mirrors planner/router.py: a single local backend, no failover.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from shorts_pipeline.config.settings import Settings
from shorts_pipeline.logging_setup import get_logger
from shorts_pipeline.planner.schema import NarrationPlan
from shorts_pipeline.publisher.schema import PublishingPackage
from shorts_pipeline.publisher.vllm_client import VllmPublisherClient

log = get_logger(__name__)

_VALID_BACKENDS = frozenset({"vllm", "local", "auto", "ollama"})


@runtime_checkable
class PublisherClient(Protocol):
    def generate_package(
        self,
        figure_name: str,
        plan: NarrationPlan,
    ) -> PublishingPackage: ...


def build_publisher_client(settings: Settings) -> PublisherClient:
    """Return the vLLM publisher client."""
    backend = (settings.planner_backend or "vllm").lower().strip()
    if backend not in _VALID_BACKENDS:
        raise ValueError(
            f"planner_backend={backend!r} is not supported by the publisher. "
            "Only local vLLM is available — set SHORTS_PLANNER_BACKEND=vllm."
        )
    if backend == "ollama":
        log.warning(
            "publisher_backend_ollama_deprecated",
            message="SHORTS_PLANNER_BACKEND=ollama is deprecated; use vllm",
        )
    log.info(
        "publisher_backend_vllm",
        model=settings.local_llm_model,
        base_url=settings.local_llm_base_url,
    )
    return VllmPublisherClient(settings)
