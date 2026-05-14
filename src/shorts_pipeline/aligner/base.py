"""Pluggable forced-alignment backends."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from shorts_pipeline.config.settings import Settings


@dataclass(frozen=True)
class WordSpan:
    word: str
    start_s: float
    end_s: float


@runtime_checkable
class AlignerBackend(Protocol):
    def align_words(self, audio_path: Path, reference_text: str) -> list[WordSpan]: ...


def load_aligner(settings: Settings) -> AlignerBackend:
    backend = settings.aligner_backend.lower().strip()
    if backend == "faster_whisper":
        from shorts_pipeline.aligner.faster_whisper_backend import FasterWhisperAligner

        return FasterWhisperAligner(settings)
    raise ValueError(
        f"Unknown aligner backend {settings.aligner_backend!r}. "
        "Install optional deps: pip install -e \".[align]\" and use faster_whisper."
    )
