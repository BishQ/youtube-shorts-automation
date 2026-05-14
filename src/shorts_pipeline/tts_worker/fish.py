"""Fish Speech (or compatible) HTTP TTS client."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from shorts_pipeline.config.settings import Settings
from shorts_pipeline.media.ffprobe import FFprobeError, ffprobe_duration_s


class FishTTSError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None, detail: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail


class FishTTSClient:
    def __init__(self, settings: Settings) -> None:
        self._s = settings

    def synthesize_wav(self, text: str, out_path: Path) -> Path:
        base = self._s.fish_base_url.rstrip("/")
        path = self._s.fish_tts_path if self._s.fish_tts_path.startswith("/") else f"/{self._s.fish_tts_path}"
        url = f"{base}{path}"
        payload: dict[str, Any] = {"text": text, "format": "wav"}
        if self._s.fish_reference_id:
            payload["reference_id"] = self._s.fish_reference_id

        try:
            with httpx.Client(timeout=self._s.fish_timeout_s) as client:
                r = client.post(url, json=payload)
        except httpx.TimeoutException as e:
            raise FishTTSError(f"Fish TTS timeout: {e}") from e
        except httpx.RequestError as e:
            raise FishTTSError(f"Fish TTS connection error: {e}") from e

        if r.status_code >= 500:
            raise FishTTSError(
                f"Fish TTS server error HTTP {r.status_code}",
                status_code=r.status_code,
                detail=r.text[:2000],
            )
        if r.status_code >= 400:
            raise FishTTSError(
                f"Fish TTS client error HTTP {r.status_code}",
                status_code=r.status_code,
                detail=r.text[:2000],
            )

        out_path.parent.mkdir(parents=True, exist_ok=True)
        # Some servers return raw audio; others JSON with base64 — support raw first
        ct = (r.headers.get("content-type") or "").lower()
        if "application/json" in ct:
            raise FishTTSError(
                "Fish TTS returned JSON; extend fish.py to decode your server's schema",
                detail=r.text[:2000],
            )
        data = r.content
        if not data:
            raise FishTTSError("Fish TTS returned empty body")
        out_path.write_bytes(data)

        if out_path.stat().st_size < 256:
            raise FishTTSError(f"Fish TTS output too small/corrupt: {out_path}")

        try:
            dur = ffprobe_duration_s(ffprobe_path=self._s.ffprobe_path, media_path=out_path)
        except FFprobeError as e:
            raise FishTTSError(f"ffprobe validation failed for TTS output: {e}") from e
        if dur <= 0.05:
            raise FishTTSError(f"TTS audio duration invalid: {dur}s")

        return out_path
