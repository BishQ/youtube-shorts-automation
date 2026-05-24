"""Ollama planner client — local OpenAI-compatible HTTP API (port 11434)."""

from __future__ import annotations

from shorts_pipeline.planner.vllm_client import VllmPlannerClient, VllmPlannerError

OllamaPlannerError = VllmPlannerError


class OllamaPlannerClient(VllmPlannerClient):
    """Same HTTP API as vLLM; Ollama serves on http://127.0.0.1:11434/v1."""
