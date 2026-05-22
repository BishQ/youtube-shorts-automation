"""Data models for the editor stage."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from shorts_pipeline.planner.schema import (
    AudioEvent,
    CameraMotion,
    ColorGrade,
    EmotionType,
    LutChoice,
    SubtitlePosition,
    TransitionType,
)


@dataclass
class ClipSpec:
    index: int
    image_path: Path
    duration_s: float
    cut_at_s: float
    camera: CameraMotion
    transition_in: TransitionType
    audio_event: AudioEvent
    subtitle_position: SubtitlePosition
    emphasis_words: list[str] = field(default_factory=list)
    intensity: float = 0.5
    emotion: EmotionType = EmotionType.reflective
    color_grade: ColorGrade | None = None
    xfade_effect_name: str | None = None
    # Wan I2V output — when set and the file exists, render uses MP4 instead of Ken Burns PNG.
    video_path: Path | None = None


@dataclass
class EditPlan:
    clips: list[ClipSpec]
    lut_choice: LutChoice
    end_plate_question: str
    narration_duration_s: float
    bgm_path: Path
