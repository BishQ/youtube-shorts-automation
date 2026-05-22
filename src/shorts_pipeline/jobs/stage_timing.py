"""Human-readable labels & ordering for pipeline stage duration analytics."""

from __future__ import annotations

from shorts_pipeline.jobs.models import PipelineStage

# Visual order matching the timeline (research-style naming for creators).
_STAGE_ORDER = [
    PipelineStage.plan,
    PipelineStage.images,
    PipelineStage.tts,
    PipelineStage.align,
    PipelineStage.i2v,
    PipelineStage.render,
    PipelineStage.publish,
]

STAGE_TIMING_LABELS: dict[str, str] = {
    "plan": "Script & planning",
    "images": "Image generation",
    "tts": "Voiceover (TTS)",
    "align": "Subtitle timing",
    "i2v": "Image-to-video (Wan)",
    "render": "Video render & mux",
    "publish": "Packaging & metadata",
}


def timing_label(stage: PipelineStage | str) -> str:
    key = stage.value if isinstance(stage, PipelineStage) else stage
    return STAGE_TIMING_LABELS.get(key, key.replace("_", " ").title())


def ordered_stage_keys() -> list[str]:
    return [s.value for s in _STAGE_ORDER]
