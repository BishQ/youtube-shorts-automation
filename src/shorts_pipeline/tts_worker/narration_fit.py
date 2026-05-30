"""Duration auto-fit for narration.wav — measure, then atempo to the target band.

Why: a fixed ``kokoro_speed`` can't hit a duration target because different
scripts have different word/syllable counts. Rather than guess a speed up front,
we synthesize once, **measure** the result, then stretch the audio with FFmpeg
``atempo`` (pitch-preserved) so every Short lands in the same window:

* short scripts are **slowed** (atempo < 1) to fill toward the target,
* long scripts are **sped up** (atempo > 1) so the ending never gets truncated
  at the render cap.

``atempo`` is linear (factor = current / target) and deterministic, so one pass
lands the duration exactly — no iteration, unlike re-rendering Kokoro at a new
speed. When the clamped atempo still leaves the audio over the hard cap, we fall
back to a hard cut + 300 ms fadeout (a clipped ending beats a >60 s Short that
YouTube demotes).

The original synthesis is preserved as ``narration_original.wav``.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from shorts_pipeline.config.settings import Settings
from shorts_pipeline.logging_setup import get_logger
from shorts_pipeline.media.ffprobe import ffprobe_duration_s

log = get_logger(__name__)


@dataclass
class NarrationFitResult:
    original_s: float
    final_s: float
    atempo: float
    action: str  # "in_band" | "stretched" | "stretched_underfilled" | "hard_cut" | "disabled"


@dataclass
class FitPlan:
    atempo: float
    action: str  # "in_band" | "stretch" | "stretch_underfilled" | "hard_cut"
    predicted_s: float


def plan_narration_fit(original_s: float, settings: Settings) -> FitPlan:
    """Pure decision: given the measured duration, return the atempo + action.

    ``atempo`` is linear (final = original / atempo), so the post-stretch length
    is predictable without running FFmpeg — which makes the whole policy unit
    testable and lets us decide the hard-cut up front.
    """
    lo = settings.narration_fit_band_min_s
    hi = settings.narration_fit_band_max_s
    if lo <= original_s <= hi:
        return FitPlan(1.0, "in_band", original_s)

    target = settings.narration_fit_target_s
    raw = original_s / target if target > 0 else 1.0
    atempo = min(settings.narration_fit_atempo_max, max(settings.narration_fit_atempo_min, raw))
    predicted = original_s / atempo if atempo > 0 else original_s

    if predicted > settings.narration_fit_hard_cap_s + 1e-3:
        return FitPlan(atempo, "hard_cut", settings.narration_fit_hard_cap_s)
    if predicted < lo - 1e-3:
        return FitPlan(atempo, "stretch_underfilled", predicted)
    return FitPlan(atempo, "stretch", predicted)


def _probe(settings: Settings, wav: Path) -> float:
    return ffprobe_duration_s(ffprobe_path=settings.ffprobe_path, media_path=wav)


def _run(cmd: list[str]) -> None:
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed ({res.returncode}): {(res.stderr or '')[-600:]}"
        )


def fit_narration(wav: Path, settings: Settings) -> NarrationFitResult:
    """Stretch ``wav`` in place toward the configured duration band.

    Returns a :class:`NarrationFitResult` describing what happened. The file at
    ``wav`` is replaced with the fitted audio; the pre-fit synthesis is kept at
    ``narration_original.wav`` beside it.
    """
    original = _probe(settings, wav)

    if not settings.narration_fit_enabled:
        return NarrationFitResult(original, original, 1.0, "disabled")

    plan = plan_narration_fit(original, settings)
    if plan.action == "in_band":
        log.info(
            "narration_fit_in_band",
            duration_s=round(original, 2),
            band=(settings.narration_fit_band_min_s, settings.narration_fit_band_max_s),
        )
        return NarrationFitResult(original, original, 1.0, "in_band")

    backup = wav.parent / "narration_original.wav"
    if not backup.exists():
        import shutil

        shutil.copy2(wav, backup)

    tmp = wav.parent / "narration_fit.tmp.wav"
    _run([
        settings.ffmpeg_path, "-y", "-v", "error",
        "-i", str(wav),
        "-filter:a", f"atempo={plan.atempo:.4f}",
        "-ar", "24000",
        str(tmp),
    ])

    if plan.action == "hard_cut":
        # atempo at its ceiling still too long → trim with a short fadeout.
        fade = 0.3
        hard_cap = settings.narration_fit_hard_cap_s
        cut = wav.parent / "narration_cut.tmp.wav"
        _run([
            settings.ffmpeg_path, "-y", "-v", "error",
            "-i", str(tmp),
            "-t", f"{hard_cap:.3f}",
            "-af", f"afade=t=out:st={max(0.0, hard_cap - fade):.3f}:d={fade:.3f}",
            "-ar", "24000",
            str(cut),
        ])
        tmp.unlink(missing_ok=True)
        cut.replace(wav)
        final = _probe(settings, wav)
        log.warning(
            "narration_fit_hard_cut",
            original_s=round(original, 2),
            atempo=round(plan.atempo, 3),
            final_s=round(final, 2),
            hard_cap_s=hard_cap,
        )
        return NarrationFitResult(original, final, plan.atempo, "hard_cut")

    tmp.replace(wav)
    final = _probe(settings, wav)
    action = "stretched" if plan.action == "stretch" else "stretched_underfilled"
    log.info(
        "narration_fit_stretched",
        original_s=round(original, 2),
        target_s=settings.narration_fit_target_s,
        atempo=round(plan.atempo, 3),
        final_s=round(final, 2),
        action=action,
    )
    return NarrationFitResult(original, final, plan.atempo, action)
