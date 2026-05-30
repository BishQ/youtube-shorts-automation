"""Job and pipeline enums / DTOs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
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
    """Pipeline gates. Execution order is ``PIPELINE_STAGE_ORDER`` (not enum order)."""

    plan = "plan"
    tts = "tts"
    images = "images"
    align = "align"
    i2v = "i2v"
    render = "render"
    publish = "publish"


# Voice before images in the linear fallback; when pipeline_parallel_stages is on,
# TTS and images run together after plan (images only need plan.json).
PIPELINE_STAGE_ORDER: tuple[PipelineStage, ...] = (
    PipelineStage.plan,
    PipelineStage.tts,
    PipelineStage.images,
    PipelineStage.align,
    PipelineStage.i2v,
    PipelineStage.render,
    PipelineStage.publish,
)


def pipeline_stage_order() -> list[PipelineStage]:
    return list(PIPELINE_STAGE_ORDER)


class ArtifactType(StrEnum):
    plan_json = "plan_json"
    image_png = "image_png"
    video_mp4 = "video_mp4"
    narration_wav = "narration_wav"
    subtitles_ass = "subtitles_ass"
    final_mp4 = "final_mp4"
    final_long_mp4 = "final_long_mp4"
    final_wan_mp4 = "final_wan_mp4"
    final_wan_long_mp4 = "final_wan_long_mp4"
    job_config = "job_config"
    clause_timings_json = "clause_timings_json"
    edit_plan_json = "edit_plan_json"
    publish_package_json = "publish_package_json"


def next_stage(current: PipelineStage | None) -> PipelineStage | None:
    order = pipeline_stage_order()
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
    # Planner niche slug (documentary, crime, history, …). Drives prompts + word caps.
    niche: str = "documentary"
    # Legacy mirror kept for DB column + folder layout; new jobs set both to the same slug.
    topic_type: str = "documentary"
    language: str = "en"
    watermark_enabled: bool = False
    end_plate_enabled: bool = True
    comfy_workflow_name: str | None = None
    overlay_enabled: bool = True

    def planner_niche(self) -> str:
        from shorts_pipeline.planner.niche_resolve import resolve_niche

        return resolve_niche(self.niche or self.topic_type)


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
