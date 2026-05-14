#!/usr/bin/env python3
"""
Re-render test using existing August Caesar assets.

Steps:
  1. Load plan + timings from disk (bypasses NarrationPlan validators).
  2. Re-run faster_whisper aligner to get fresh word spans.
  3. Regenerate subtitles.ass with the new fast (30 ms) fades.
  4. Build EditPlan with the user-specified transitions.
  5. Render to  data/jobs/august-ceaser-69a55ddc/final_test.mp4

Usage:
  python test_render.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(ROOT / "src"))

# ── Paths ──────────────────────────────────────────────────────────────────────
JOB_DIR      = ROOT / "data" / "jobs" / "donald-trump-272962f5"
BGM_PATH     = ROOT / "extra tools" / "bgm.mp3"
OUT_MP4      = JOB_DIR / "final_test.mp4"
ASS_TEST     = JOB_DIR / "subtitles_test.ass"
NARR_WAV     = JOB_DIR / "narration.wav"
IMG_DIR      = JOB_DIR / "images"
PLAN_JSON    = JOB_DIR / "plan.json"
TIMINGS_JSON = JOB_DIR / "clause_timings.json"


def main() -> None:
    from shorts_pipeline.aligner.ass import build_ass_karaoke
    from shorts_pipeline.aligner.base import load_aligner
    from shorts_pipeline.config.settings import get_settings
    from shorts_pipeline.editor.models import ClipSpec, EditPlan
    from shorts_pipeline.editor.pacing import compute_cut_times_from_ranges
    from shorts_pipeline.planner.schema import (
        AudioEvent, Beat, Clause, CameraMotion, EmotionType,
        LutChoice, NarrationPlan, SubtitlePosition, TransitionType,
    )
    from shorts_pipeline.renderer.ffmpeg import RenderRequest, render_short

    settings = get_settings()
    settings = settings.model_copy(update={
        "overlay_enabled": True,   # snow overlay on
        "end_plate_enabled": True,
    })

    # ── Load raw data ──────────────────────────────────────────────────────────
    plan_raw    = json.loads(PLAN_JSON.read_text(encoding="utf-8"))
    timings_raw = json.loads(TIMINGS_JSON.read_text(encoding="utf-8"))

    n = len(plan_raw["clauses"])
    raw_ranges = [(float(r[0]), float(r[1])) for r in timings_raw]
    narration_duration_s = max(end for _, end in raw_ranges)
    # Normalize: each clip runs until the NEXT clause starts; last runs to narration end.
    # This fills the silence gaps and gives every image its correct screen time.
    norm_ranges = compute_cut_times_from_ranges(raw_ranges, narration_duration_s)
    image_paths = [IMG_DIR / f"clause_{i:03d}.png" for i in range(n)]

    print(f"[INFO] {n} clauses  |  narration {narration_duration_s:.2f}s")
    for p in image_paths:
        if not p.is_file():
            sys.exit(f"[ERROR] Missing image: {p}")

    # ── Transitions per user spec ──────────────────────────────────────────────
    # clip[0] = first image, always hard_cut (no incoming transition)
    # Image 1→2  = Paint Splatter    (clip[1])
    # Image 2→3  = VR Light Rays     (clip[2])
    # Image 3→4  = Random Blocks     (clip[3])
    # Image 4→5  = Center Split      (clip[4])
    # Image 5→6  = Paint Splatter    (clip[5])
    # Image 6→7  = VR Light Rays     (clip[6])
    # Image 7→8  = Random Blocks     (clip[7])
    # Image 8→9  = Center Split      (clip[8])
    # Image 9→10 = Random Blocks     (clip[9])
    # Image 10→11= VR Light Rays     (clip[10])
    # All remaining → Center Split
    _spec = [
        TransitionType.hard_cut,      # clip 0
        TransitionType.paint_splatter, # 1→2
        TransitionType.vr_light_rays,  # 2→3
        TransitionType.random_blocks,  # 3→4
        TransitionType.center_split,   # 4→5
        TransitionType.paint_splatter, # 5→6
        TransitionType.vr_light_rays,  # 6→7
        TransitionType.random_blocks,  # 7→8
        TransitionType.center_split,   # 8→9
        TransitionType.random_blocks,  # 9→10
        TransitionType.vr_light_rays,  # 10→11
    ]
    def _trans(i: int) -> TransitionType:
        if i < len(_spec):
            return _spec[i]
        return TransitionType.center_split

    # ── Build ClipSpec list ────────────────────────────────────────────────────
    clips: list[ClipSpec] = []
    for i, (norm_range, clause_raw, img_path) in enumerate(
        zip(norm_ranges, plan_raw["clauses"], image_paths)
    ):
        start_s, end_s = norm_range
        beat_raw = clause_raw["beat"]
        clips.append(ClipSpec(
            index=i,
            image_path=img_path,
            duration_s=max(0.05, end_s - start_s),
            cut_at_s=start_s,
            camera=CameraMotion(beat_raw["camera"]),
            transition_in=_trans(i),
            audio_event=AudioEvent(beat_raw.get("audio_event", "none")),
            subtitle_position=SubtitlePosition(beat_raw.get("subtitle_position", "bottom")),
            emphasis_words=list(beat_raw.get("emphasis_words", [])),
            intensity=float(beat_raw.get("intensity", 0.5)),
            emotion=EmotionType(beat_raw["emotion"]),
        ))

    edit = EditPlan(
        clips=clips,
        lut_choice=LutChoice(plan_raw["lut_choice"]),
        end_plate_question=plan_raw["end_plate_question"],
        narration_duration_s=narration_duration_s,
        bgm_path=BGM_PATH,
    )

    # ── Regenerate ASS with new fast fades ────────────────────────────────────
    print("[ALIGN] Running faster_whisper to get fresh word spans …")
    aligner = load_aligner(settings)
    words = aligner.align_words(NARR_WAV, plan_raw["full_script"])
    print(f"[ALIGN] Got {len(words)} word spans")

    # Build a minimal NarrationPlan (model_construct skips all validators)
    plan_clauses = []
    for c_raw in plan_raw["clauses"]:
        beat = Beat.model_validate(c_raw["beat"])
        plan_clauses.append(Clause.model_construct(
            text=c_raw["text"],
            image_prompt=c_raw["image_prompt"],
            beat=beat,
        ))

    plan_obj = NarrationPlan.model_construct(
        historical_figure=plan_raw["historical_figure"],
        cold_open_object=plan_raw.get("cold_open_object", ""),
        decision_lever=None,
        clauses=plan_clauses,
        full_script=plan_raw["full_script"],
        lut_choice=LutChoice(plan_raw["lut_choice"]),
        end_plate_question=plan_raw["end_plate_question"],
    )

    build_ass_karaoke(plan_obj, words, settings, ASS_TEST)
    print(f"[ASS]   Written -> {ASS_TEST.name}")

    # ── Render ─────────────────────────────────────────────────────────────────
    req = RenderRequest(
        edit=edit,
        narration_wav=NARR_WAV,
        ass_path=ASS_TEST,
        out_mp4=OUT_MP4,
        narration_duration_s=narration_duration_s,
    )
    print(f"[RENDER] Starting -> {OUT_MP4.name}  (this will take a few minutes)")
    render_short(req, settings)
    print(f"[RENDER] Done!  ->  {OUT_MP4}")


if __name__ == "__main__":
    main()
