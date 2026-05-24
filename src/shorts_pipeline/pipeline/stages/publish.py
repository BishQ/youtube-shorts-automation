"""Publish stage adapter."""

from __future__ import annotations

from shorts_pipeline.orchestrator import PipelineOrchestrator


def run(orchestrator: PipelineOrchestrator, job_id: str) -> None:
    orchestrator.run_publish(job_id)
