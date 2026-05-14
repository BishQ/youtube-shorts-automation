"""Optional BGM beat snapping for cut times (Rule D: narration-first + ±80 ms snap)."""

from __future__ import annotations

from pathlib import Path


def snap_to_beat(cut_time_s: float, bgm_path: Path, window_s: float = 0.08) -> float:
    """
    Return cut_time_s snapped to the nearest BGM beat within window_s.
    Returns the original time unchanged if librosa is not installed, the BGM
    file can't be read, or no beat falls within the window.
    """
    try:
        import librosa  # type: ignore

        y, sr = librosa.load(str(bgm_path), sr=None, mono=True)
        _, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
        beat_times: list[float] = librosa.frames_to_time(beat_frames, sr=sr).tolist()

        if not beat_times:
            return cut_time_s

        nearest_idx = min(range(len(beat_times)), key=lambda i: abs(beat_times[i] - cut_time_s))
        if abs(beat_times[nearest_idx] - cut_time_s) <= window_s:
            return float(beat_times[nearest_idx])
        return cut_time_s
    except Exception:  # noqa: BLE001
        return cut_time_s
