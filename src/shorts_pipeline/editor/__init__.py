"""Editor stage: converts beat metadata + timings into a concrete EditPlan."""

from __future__ import annotations

import random
from pathlib import Path

from shorts_pipeline.editor.models import ClipSpec, EditPlan
from shorts_pipeline.editor.pacing import compute_cut_times_from_ranges
from shorts_pipeline.editor.xfade_effects import pick_random_xfade_effect
from shorts_pipeline.planner.schema import NarrationPlan, SubtitlePosition, TransitionType

__all__ = ["ClipSpec", "EditPlan", "build_edit_plan_from_ranges"]

# Fixed transition ladder when ``build_edit_plan_from_ranges(...,
# randomize_transitions=False)``.  Index 0 = clip[0] (opening, hard_cut).
# When ``randomize_transitions=True`` (default in Settings), this tuple is not used.
_FIXED_TRANSITIONS: tuple[TransitionType, ...] = (
    TransitionType.hard_cut,       # clip 0  — opening frame, no blend
    TransitionType.paint_splatter, # clip 1  (image 1 → 2)
    TransitionType.vr_light_rays,  # clip 2  (image 2 → 3)
    TransitionType.random_blocks,  # clip 3  (image 3 → 4)
    TransitionType.center_split,   # clip 4  (image 4 → 5)
    TransitionType.paint_splatter, # clip 5  (image 5 → 6)
    TransitionType.vr_light_rays,  # clip 6  (image 6 → 7)
    TransitionType.random_blocks,  # clip 7  (image 7 → 8)
    TransitionType.center_split,   # clip 8  (image 8 → 9)
    TransitionType.random_blocks,  # clip 9  (image 9 → 10)
    TransitionType.vr_light_rays,  # clip 10 (image 10 → 11)
    TransitionType.paint_splatter, # clip 11 (image 11 → 12)
    # clip 12+ → center_split (handled by the fallback below)
)
_FALLBACK_TRANSITION = TransitionType.center_split


def _fixed_transition(clip_index: int) -> TransitionType:
    if clip_index < len(_FIXED_TRANSITIONS):
        return _FIXED_TRANSITIONS[clip_index]
    return _FALLBACK_TRANSITION


def build_edit_plan_from_ranges(
    plan: NarrationPlan,
    image_paths: list[Path],
    ranges: list[tuple[float, float]],
    narration_duration_s: float,
    bgm_path: Path,
    *,
    snap_to_bgm_beats: bool = False,
    run_face_detection: bool = True,
    randomize_transitions: bool = False,
    transition_random_seed: int | None = None,
) -> EditPlan:
    """
    Build an EditPlan from precomputed clause time ranges.

    Args:
        plan: NarrationPlan containing per-clause beat metadata.
        image_paths: One image per clause, in order.
        ranges: (start_s, end_s) per clause from clause_timings.json.
        narration_duration_s: Total narration audio length in seconds.
        bgm_path: Path to the BGM file (used for beat snapping if enabled).
        snap_to_bgm_beats: If True and librosa is installed, snap cut times to
            the nearest BGM beat within ±80 ms.
        run_face_detection: If True and mediapipe/cv2 are installed, override
            subtitle_position based on face location in each image.
        randomize_transitions: If True, clip 0 stays ``hard_cut``; every later
            clip uses a random FFmpeg ``xfade`` preset (see ``xfade_effects``).
        transition_random_seed: RNG seed when ``randomize_transitions`` is True;
            if None, uses an arbitrary seed (callers should pass a job-derived
            seed for reproducibility).
    """
    from shorts_pipeline.editor.face_locator import detect_subtitle_position

    rng = random.Random(transition_random_seed) if randomize_transitions else None

    normalized = compute_cut_times_from_ranges(ranges, narration_duration_s)

    clips: list[ClipSpec] = []
    for i, (clause, img_path, (start_s, end_s)) in enumerate(
        zip(plan.clauses, image_paths, normalized)
    ):
        beat = clause.beat

        cut_at = start_s
        if snap_to_bgm_beats:
            from shorts_pipeline.editor.beat_detector import snap_to_beat
            cut_at = snap_to_beat(cut_at, bgm_path)

        # Subtitle position: respect beat value unless it's the default (bottom)
        # and face detection is available — then let the detector override.
        sub_pos = beat.subtitle_position
        if run_face_detection and sub_pos == SubtitlePosition.bottom:
            sub_pos = detect_subtitle_position(img_path)

        trans_in = _fixed_transition(i)
        xfade_name: str | None = None
        if rng is not None and i > 0:
            trans_in = TransitionType.xfade
            xfade_name = pick_random_xfade_effect(rng)

        clips.append(
            ClipSpec(
                index=i,
                image_path=img_path,
                duration_s=max(0.05, end_s - start_s),
                cut_at_s=cut_at,
                camera=beat.camera,
                transition_in=trans_in,
                audio_event=beat.audio_event,
                subtitle_position=sub_pos,
                emphasis_words=list(beat.emphasis_words),
                intensity=beat.intensity,
                emotion=beat.emotion,
                color_grade=beat.color_grade,
                xfade_effect_name=xfade_name,
            )
        )

    return EditPlan(
        clips=clips,
        lut_choice=plan.lut_choice,
        end_plate_question=plan.end_plate_question,
        narration_duration_s=narration_duration_s,
        bgm_path=bgm_path,
    )
