"""Shared API request models for the FastAPI control plane."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from shorts_pipeline.config.settings import get_settings


class CreateJobBody(BaseModel):
    figure_name: str = Field(..., min_length=1)
    bgm_path: str = Field(..., min_length=1)
    topic_type: str = "historical_figure"
    language: str = "en"
    watermark_enabled: bool = Field(default_factory=lambda: get_settings().watermark_enabled)
    end_plate_enabled: bool = Field(default_factory=lambda: get_settings().end_plate_enabled)
    comfy_workflow_name: str | None = None
    overlay_enabled: bool = True


class UiPatchBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    comfy_workflow_name: str | None = None
    whisper_model_size: str | None = None


class CreateBatchBody(BaseModel):
    name: str = Field(..., min_length=1)
    topics: list[str] = Field(..., min_length=1)
    bgm_path: str = Field(..., min_length=1)
    topic_type: str = "historical_figure"
    language: str = "en"


class StartBatchBody(BaseModel):
    from_index: int = Field(default=0, ge=0)
