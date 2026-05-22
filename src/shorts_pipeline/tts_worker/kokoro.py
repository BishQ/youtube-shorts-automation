"""
Kokoro TTS client — two modes:

  MODE 1 (default): direct Python API
    Requires:  pip install kokoro soundfile numpy
    No server needed. KPipeline runs in-process.

  MODE 2: HTTP (kokoro-fastapi or any OpenAI-compatible TTS endpoint)
    Requires:  a running kokoro-fastapi server
    Enable:    SHORTS_TTS_BACKEND=kokoro_http
               SHORTS_KOKORO_HTTP_BASE_URL=http://127.0.0.1:8880
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from shorts_pipeline.config.settings import Settings
from shorts_pipeline.logging_setup import get_logger
from shorts_pipeline.media.ffprobe import FFprobeError, ffprobe_duration_s

log = get_logger(__name__)


class KokoroTTSError(Exception):
    def __init__(self, message: str, *, detail: Any = None) -> None:
        super().__init__(message)
        self.detail = detail


class KokoroTTSClient:
    """
    Synthesize speech with Kokoro TTS.

    Picks the mode from Settings.tts_backend:
      "kokoro"      → direct Python API (in-process, no server)
      "kokoro_http" → HTTP to kokoro-fastapi (OpenAI TTS endpoint)
    """

    def __init__(self, settings: Settings) -> None:
        self._s = settings

    def synthesize_wav(self, text: str, out_path: Path) -> Path:
        if self._s.tts_backend == "kokoro_http":
            return self._synthesize_http(text, out_path)
        return self._synthesize_python(text, out_path)

    # ── Direct Python API ─────────────────────────────────────────────────────

    def _synthesize_python(self, text: str, out_path: Path) -> Path:
        try:
            from kokoro import KPipeline  # type: ignore
        except ImportError as e:
            raise KokoroTTSError(
                "kokoro package not installed. Run: pip install kokoro soundfile numpy"
            ) from e

        try:
            import numpy as np  # type: ignore
            import soundfile as sf  # type: ignore
        except ImportError as e:
            raise KokoroTTSError(
                "soundfile/numpy not installed. Run: pip install soundfile numpy"
            ) from e

        log.info("kokoro_synthesize", mode="python", voice=self._s.kokoro_voice)

        try:
            # Pin torch's RNG so the same (text, voice, speed) tuple renders
            # byte-identical audio across processes. Without this, Kokoro's
            # internal sampling drifts 3-5 s per render, making fit_narration's
            # duration-based speed math unreliable for the 80k-video pipeline.
            import torch  # type: ignore
            torch.manual_seed(getattr(self._s, "kokoro_seed", 42))

            pipeline = KPipeline(lang_code=self._s.kokoro_lang_code)
            generator = pipeline(
                text,
                voice=self._s.kokoro_voice,
                speed=self._s.kokoro_speed,
            )
            chunks = [audio for _, _, audio in generator]
        except Exception as e:
            raise KokoroTTSError(f"Kokoro synthesis failed: {e}") from e

        if not chunks:
            raise KokoroTTSError("Kokoro produced no audio chunks")

        audio = np.concatenate(chunks)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(out_path), audio, 24000)

        return self._validate(out_path)

    # ── HTTP (kokoro-fastapi) ─────────────────────────────────────────────────

    def _synthesize_http(self, text: str, out_path: Path) -> Path:
        try:
            import httpx
        except ImportError as e:
            raise KokoroTTSError("httpx not installed. Run: pip install httpx") from e

        base = self._s.kokoro_http_base_url.rstrip("/")
        url = f"{base}/v1/audio/speech"
        payload = {
            "model": "kokoro",
            "input": text,
            "voice": self._s.kokoro_voice,
            "response_format": "wav",
            "speed": self._s.kokoro_speed,
        }
        log.info("kokoro_synthesize", mode="http", url=url, voice=self._s.kokoro_voice)

        try:
            with httpx.Client(timeout=self._s.kokoro_http_timeout_s) as client:
                r = client.post(url, json=payload)
        except httpx.TimeoutException as e:
            raise KokoroTTSError(f"Kokoro HTTP timeout: {e}") from e
        except httpx.RequestError as e:
            raise KokoroTTSError(f"Kokoro HTTP connection error: {e}") from e

        if r.status_code >= 400:
            raise KokoroTTSError(
                f"Kokoro HTTP error {r.status_code}",
                detail=r.text[:2000],
            )

        data = r.content
        if not data:
            raise KokoroTTSError("Kokoro HTTP returned empty body")

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(data)

        return self._validate(out_path)

    # ── Validation ────────────────────────────────────────────────────────────

    def _validate(self, out_path: Path) -> Path:
        if not out_path.is_file() or out_path.stat().st_size < 256:
            raise KokoroTTSError(f"Kokoro output too small / missing: {out_path}")
        try:
            dur = ffprobe_duration_s(
                ffprobe_path=self._s.ffprobe_path, media_path=out_path
            )
        except FFprobeError as e:
            raise KokoroTTSError(f"ffprobe validation failed: {e}") from e
        if dur <= 0.05:
            raise KokoroTTSError(f"TTS audio duration invalid: {dur:.3f}s")
        log.info("kokoro_done", duration_s=round(dur, 2), path=str(out_path))
        return out_path
