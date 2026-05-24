"""Ollama publisher client — local OpenAI-compatible HTTP."""

from __future__ import annotations

from shorts_pipeline.publisher.vllm_client import VllmPublisherClient, VllmPublisherError

OllamaPublisherError = VllmPublisherError


class OllamaPublisherClient(VllmPublisherClient):
    """Same HTTP API as vLLM; Ollama serves on http://127.0.0.1:11434/v1."""
