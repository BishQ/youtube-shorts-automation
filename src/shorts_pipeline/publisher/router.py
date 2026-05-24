"""Publisher router — vLLM or Ollama."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from shorts_pipeline.config.settings import Settings
from shorts_pipeline.logging_setup import get_logger
from shorts_pipeline.planner.schema import NarrationPlan
from shorts_pipeline.publisher.ollama_client import OllamaPublisherClient
from shorts_pipeline.publisher.schema import PublishingPackage
from shorts_pipeline.publisher.vllm_client import VllmPublisherClient

log = get_logger(__name__)

_VALID_BACKENDS = frozenset({"vllm", "ollama", "local", "auto"})


@runtime_checkable
class PublisherClient(Protocol):
    def generate_package(
        self,
        figure_name: str,
        plan: NarrationPlan,
    ) -> PublishingPackage: ...


def build_publisher_client(settings: Settings) -> PublisherClient:
    backend = (settings.planner_backend or "vllm").lower().strip()
    if backend not in _VALID_BACKENDS:
        raise ValueError(
            f"planner_backend={backend!r} is not supported. "
            "Use SHORTS_PLANNER_BACKEND=vllm or ollama."
        )
    if backend == "ollama":
        log.info(
            "publisher_backend_ollama",
            model=settings.local_llm_model,
            base_url=settings.local_llm_base_url,
        )
        return OllamaPublisherClient(settings)
    log.info(
        "publisher_backend_vllm",
        model=settings.local_llm_model,
        base_url=settings.local_llm_base_url,
    )
    return VllmPublisherClient(settings)
