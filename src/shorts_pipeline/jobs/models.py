"""Job and pipeline enums / DTOs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class JobStatus(StrEnum):
    pending = "pending"
    running = "running"
    paused = "paused"
    completed = "completed"
    failed = "failed"


class PipelineStage(StrEnum):
    """Ordered gates. After a stage completes, next is the following enum value."""

    plan = "plan"
    images = "images"
    tts = "tts"
    align = "align"
    i2v = "i2v"
    render = "render"
    publish = "publish"


class ArtifactType(StrEnum):
    plan_json = "plan_json"
    image_png = "image_png"
    video_mp4 = "video_mp4"
    narration_wav = "narration_wav"
    subtitles_ass = "subtitles_ass"
    final_mp4 = "final_mp4"
    final_long_mp4 = "final_long_mp4"
    job_config = "job_config"
    clause_timings_json = "clause_timings_json"
    edit_plan_json = "edit_plan_json"
    publish_package_json = "publish_package_json"


def next_stage(current: PipelineStage | None) -> PipelineStage | None:
    order = list(PipelineStage)
    if current is None:
        return PipelineStage.plan
    idx = order.index(current)
    if idx + 1 < len(order):
        return order[idx + 1]
    return None


class JobConfigSnapshot(BaseModel):
    """Per-job options (from UI + env defaults). Secrets are not stored here."""

    bgm_path: str = Field(..., description="Absolute or resolved path to single BGM track")
    figure_name: str
    topic_type: str = "historical_figure"
    language: str = "en"
    watermark_enabled: bool = False
    end_plate_enabled: bool = True
    comfy_workflow_name: str | None = None
    overlay_enabled: bool = True


class JobErrorDetail(BaseModel):
    stage: str
    code: str
    message: str
    detail: dict[str, Any] | None = None


@dataclass
class JobRecord:
    id: str
    figure_name: str
    topic_type: str
    language: str
    status: JobStatus
    current_stage: PipelineStage | None
    last_completed_stage: PipelineStage | None
    error: JobErrorDetail | None
    created_at: datetime
    updated_at: datetime
    config_snapshot: JobConfigSnapshot


@dataclass
class ArtifactRow:
    id: int
    job_id: str
    stage: PipelineStage
    artifact_type: ArtifactType
    path: str
    sha256: str | None
    meta_json: str | None
    completed_at: datetime
