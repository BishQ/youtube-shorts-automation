"""faster-whisper based word alignment (optional dependency)."""

from __future__ import annotations

import re
from pathlib import Path

from shorts_pipeline.aligner.base import WordSpan
from shorts_pipeline.config.settings import Settings
from shorts_pipeline.logging_setup import get_logger

log = get_logger(__name__)


def _cublas12_loadable() -> bool:
    """ctranslate2 / faster-whisper Windows CUDA wheels expect CUDA 12 cuBLAS."""
    try:
        import ctypes

        ctypes.CDLL("cublas64_12.dll")
        return True
    except Exception:
        return False


class FasterWhisperAligner:
    def __init__(self, settings: Settings) -> None:
        try:
            from faster_whisper import WhisperModel  # noqa: F401
        except ImportError as e:
            raise ImportError(
                "faster-whisper is not installed. Install with: pip install -e \".[align]\""
            ) from e
        self._s = settings
        self._model: object | None = None
        # Set after a lazy CUDA failure during transcribe (e.g. cublas present for ctypes
        # but encoder still cannot load the runtime).
        self._force_cpu: bool = False

    def _resolve_device_compute(self) -> tuple[str, str]:
        if self._force_cpu:
            return "cpu", "int8"

        raw = (self._s.whisper_device or "cpu").lower().strip()
        if raw == "gpu":
            raw = "cuda"
        if raw == "cpu":
            return "cpu", "int8"

        if raw in ("cuda", "auto"):
            if _cublas12_loadable():
                return "cuda", "float16"
            if raw == "cuda":
                log.warning(
                    "whisper_cuda_libs_missing_using_cpu",
                    hint=(
                        "Install NVIDIA CUDA Toolkit 12.x (cuBLAS on PATH) or use "
                        "SHORTS_WHISPER_DEVICE=cpu until CUDA 12 DLLs load correctly."
                    ),
                )
            return "cpu", "int8"

        log.warning("whisper_unknown_device_using_cpu", whisper_device=raw)
        return "cpu", "int8"

    def _get_model(self) -> object:
        if self._model is None:
            from faster_whisper import WhisperModel

            device, compute = self._resolve_device_compute()
            self._model = WhisperModel(
                self._s.whisper_model_size,
                device=device,
                compute_type=compute,
            )
        return self._model

    def align_words(self, audio_path: Path, reference_text: str) -> list[WordSpan]:
        model = self._get_model()
        transcribe = getattr(model, "transcribe")
        segments_gen, _info = transcribe(
            str(audio_path),
            word_timestamps=True,
            vad_filter=False,
            language="en",
            task="transcribe",
        )
        try:
            segments = list(segments_gen)
        except RuntimeError as e:
            msg = str(e).lower()
            if not self._force_cpu and (
                "cublas" in msg or "cuda" in msg or "cudnn" in msg or "cudart" in msg
            ):
                log.warning(
                    "whisper_cuda_runtime_failed_retry_cpu",
                    error=str(e),
                )
                self._force_cpu = True
                self._model = None
                return self.align_words(audio_path, reference_text)
            raise
        spans: list[WordSpan] = []
        _WORD_RE = re.compile(r"[\w']+|[.,!?;:\"]")
        for seg in segments:
            words = getattr(seg, "words", None)
            if not words:
                continue
            for w in words:
                word = (getattr(w, "word", "") or "").strip()
                if not word:
                    continue
                start = float(getattr(w, "start"))
                end = float(getattr(w, "end"))
                if end <= start:
                    continue
                spans.append(WordSpan(word=word, start_s=start, end_s=end))

        if not spans:
            raise RuntimeError("faster-whisper returned no word timestamps; check audio and model")

        spans = _snap_spans_to_reference(spans, reference_text)
        return spans


def _normalize_token(t: str) -> str:
    # Strip apostrophes too so "it's"/"its", "Caesar's"/"Caesars" etc. compare equal
    return re.sub(r"[^a-z0-9]+", "", t.lower())


def _snap_spans_to_reference(spans: list[WordSpan], reference_text: str) -> list[WordSpan]:
    """Greedy alignment: map each reference token to the next matching whisper token.

    j is only advanced when a match is found.  A failed lookup leaves j unchanged so
    the next reference token re-searches from the same position, preventing the
    cascading desync that occurred when j was unconditionally advanced to limit.
    """
    ref_tokens = [_normalize_token(x) for x in reference_text.split() if x.strip()]
    ref_tokens = [t for t in ref_tokens if t]
    out: list[WordSpan] = []
    j = 0
    for rt in ref_tokens:
        found: WordSpan | None = None
        # Search within a look-ahead window without mutating j until a match is found.
        # Using a larger window (12) handles cases where whisper inserts extra tokens
        # (e.g. splits "Abdul-Jabbar" into three separate words).
        for k in range(j, min(j + 12, len(spans))):
            st = _normalize_token(re.sub(r"^[^A-Za-z0-9]+|[^A-Za-z0-9]+$", "", spans[k].word))
            if st == rt or rt.startswith(st) or st.startswith(rt):
                found = WordSpan(word=spans[k].word, start_s=spans[k].start_s, end_s=spans[k].end_s)
                j = k + 1
                break
        if found is None:
            prev_end = out[-1].end_s if out else 0.0
            # Placeholder — actual time is interpolated in the post-pass below.
            out.append(WordSpan(word=rt, start_s=prev_end, end_s=prev_end + 0.01))
        else:
            out.append(found)

    # ── Post-pass: interpolate dummy spans (end - start == 0.01) ──────────────
    # A dummy span has no real timestamp from Whisper.  Spread them evenly over
    # the gap between the surrounding real spans so they get visible durations.
    n = len(out)
    i = 0
    while i < n:
        if abs((out[i].end_s - out[i].start_s) - 0.01) < 1e-6:
            # Find the run of dummies
            run_start = i
            while i < n and abs((out[i].end_s - out[i].start_s) - 0.01) < 1e-6:
                i += 1
            run_end = i  # exclusive
            run_len = run_end - run_start

            t_before = out[run_start - 1].end_s if run_start > 0 else 0.0
            t_after = out[run_end].start_s if run_end < n else out[-1].end_s + 0.3
            slot = max(0.06, (t_after - t_before) / (run_len + 1))
            for k, idx in enumerate(range(run_start, run_end)):
                s = t_before + slot * (k + 1) - slot * 0.5
                out[idx] = WordSpan(word=out[idx].word, start_s=s, end_s=s + slot * 0.9)
        else:
            i += 1

    return out
