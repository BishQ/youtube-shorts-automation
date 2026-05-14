"""
Quick test render using Henry Ford's existing artifacts.
Runs the render stage + long-version pass without touching the DB.

Usage:
    python test_render_henry.py
"""

from __future__ import annotations

import json
import subprocess
import wave
from pathlib import Path

# ── resolve repo root ─────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.resolve()
JOB_DIR = ROOT / "data" / "jobs" / "henry-ford-d3046068"
BGM_PATH = ROOT / "extra tools" / "bgm.mp3"
OUTRO_IMG = ROOT / "assets" / "outro.png"
OUT_SHORT = JOB_DIR / "test_final_shorts.mp4"
OUT_LONG  = JOB_DIR / "test_final_long.mp4"

import sys
sys.path.insert(0, str(ROOT / "src"))

from shorts_pipeline.config.settings import Settings
from shorts_pipeline.editor import build_edit_plan_from_ranges
from shorts_pipeline.planner.schema import NarrationPlan
from shorts_pipeline.renderer.ffmpeg import RenderRequest, render_short


def main() -> None:
    settings = Settings()

    # ── load plan ─────────────────────────────────────────────────────────────
    plan_ctx = {"allow_figure_name": bool(settings.image_prompts_include_figure_name)}
    plan: NarrationPlan = NarrationPlan.model_validate_json(
        (JOB_DIR / "plan.json").read_text(encoding="utf-8"),
        context=plan_ctx,
    )
    print(f"Plan loaded: {len(plan.clauses)} clauses, {len(plan.full_script.split())} words")

    # ── clause images in order ────────────────────────────────────────────────
    image_paths = sorted(
        JOB_DIR.glob("images/clause_*.png"),
        key=lambda p: int(p.stem.split("_")[-1]),
    )
    assert len(image_paths) == len(plan.clauses), (
        f"Image count {len(image_paths)} != clause count {len(plan.clauses)}"
    )
    print(f"Images: {len(image_paths)} found")

    # ── clause timings ────────────────────────────────────────────────────────
    raw = json.loads((JOB_DIR / "clause_timings.json").read_text(encoding="utf-8"))
    ranges: list[tuple[float, float]] = [(float(r[0]), float(r[1])) for r in raw]
    assert len(ranges) == len(plan.clauses)

    # ── narration duration from WAV ───────────────────────────────────────────
    wav_path = JOB_DIR / "narration.wav"
    with wave.open(str(wav_path), "rb") as wf:
        narration_duration_s = wf.getnframes() / float(wf.getframerate())
    range_max = max(end for _, end in ranges)
    narration_duration_s = max(narration_duration_s, range_max)
    print(f"Narration: {narration_duration_s:.2f}s")

    # ── dynamic outro duration ────────────────────────────────────────────────
    body_s = narration_duration_s + settings.last_frame_breathe_s
    hard_cap = settings.render_max_shorts_duration_s
    target = min(settings.outro_target_total_s, hard_cap)
    raw_outro = target - body_s
    outro_dur = max(settings.outro_min_duration_s, min(settings.outro_max_duration_s, raw_outro))
    outro_dur = min(outro_dur, max(0.0, hard_cap - body_s))
    render_settings = settings.model_copy(update={
        "end_plate_duration_s": outro_dur,
        "watermark_enabled": settings.watermark_enabled,
        "end_plate_enabled": settings.end_plate_enabled,
    })
    print(f"Outro: {outro_dur:.2f}s  |  Total: {body_s + outro_dur:.2f}s")

    # ── build edit plan ───────────────────────────────────────────────────────
    import hashlib
    seed = int(hashlib.sha256(b"henry-ford-test").hexdigest()[:8], 16)
    edit = build_edit_plan_from_ranges(
        plan,
        image_paths,
        ranges,
        narration_duration_s,
        bgm_path=BGM_PATH,
        snap_to_bgm_beats=render_settings.snap_to_bgm_beats,
        run_face_detection=True,
        randomize_transitions=render_settings.randomize_clip_transitions,
        transition_random_seed=seed,
    )

    # ── render Shorts version ────────────────────────────────────────────────
    outro_img = OUTRO_IMG if (OUTRO_IMG.is_file() and OUTRO_IMG.stat().st_size > 1000) else None
    req = RenderRequest(
        edit=edit,
        narration_wav=wav_path,
        ass_path=JOB_DIR / "subtitles.ass",
        out_mp4=OUT_SHORT,
        narration_duration_s=narration_duration_s,
        outro_image_path=outro_img,
    )
    print(f"\nRendering Shorts -> {OUT_SHORT.name} ...")
    render_short(req, render_settings)
    size_mb = OUT_SHORT.stat().st_size / 1_000_000
    print(f"Shorts done: {size_mb:.1f} MB")

    # render Long version (0.9x speed)
    if render_settings.render_long_version_enabled:
        speed = render_settings.render_long_version_speed
        print(f"\nRendering Long ({speed}x speed) -> {OUT_LONG.name} ...")
        cmd = [
            render_settings.ffmpeg_path, "-y",
            "-i", str(OUT_SHORT),
            "-filter_complex",
            f"[0:v]setpts=PTS/{speed}[v];[0:a]atempo={speed}[a]",
            "-map", "[v]",
            "-map", "[a]",
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k",
            str(OUT_LONG),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            print("Long version FAILED:")
            print(result.stderr[-600:])
        else:
            size_mb2 = OUT_LONG.stat().st_size / 1_000_000
            print(f"Long done: {size_mb2:.1f} MB")

    print("\nAll done.")
    print(f"  Shorts: {OUT_SHORT}")
    print(f"  Long:   {OUT_LONG}")


if __name__ == "__main__":
    main()
