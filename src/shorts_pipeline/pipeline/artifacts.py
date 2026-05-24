"""Shared artifact lookup helpers for stage services."""

from __future__ import annotations

from pathlib import Path

from shorts_pipeline.jobs.models import ArtifactType, PipelineStage
from shorts_pipeline.jobs.store import JobStore, verify_artifact_path


def latest_verified_artifact_path(
    store: JobStore,
    job_id: str,
    stage: PipelineStage,
    artifact_type: ArtifactType,
) -> Path:
    artifact = store.get_latest_artifact(job_id, stage, artifact_type)
    if artifact is None:
        raise RuntimeError(f"missing {artifact_type.value} artifact for {stage.value}")
    path = Path(artifact.path)
    if not verify_artifact_path(path, artifact.sha256):
        raise RuntimeError(f"invalid {artifact_type.value} artifact for {stage.value}: {path}")
    return path


def verified_stage_artifact_paths(
    store: JobStore,
    job_id: str,
    stage: PipelineStage,
    artifact_type: ArtifactType,
) -> list[Path]:
    paths: list[Path] = []
    for artifact in store.get_artifacts_for_stage(job_id, stage, artifact_type):
        path = Path(artifact.path)
        if not verify_artifact_path(path, artifact.sha256):
            raise RuntimeError(f"invalid {artifact_type.value} artifact for {stage.value}: {path}")
        paths.append(path)
    return paths
