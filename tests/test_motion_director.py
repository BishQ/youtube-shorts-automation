from shorts_pipeline.editor.motion_director import (
    _BUSY,
    ClipMotion,
    direct_clip_motion,
)
from shorts_pipeline.planner.schema import CameraMotion, EmotionType


def test_empty_and_single_clip() -> None:
    assert direct_clip_motion(0, seed=1) == []
    single = direct_clip_motion(1, seed=1)
    assert len(single) == 1
    assert single[0].camera == CameraMotion.ken_burns  # hook push-in


def test_no_consecutive_repeats() -> None:
    motion = direct_clip_motion(12, seed=42, emotions=[None] * 12)
    cameras = [m.camera for m in motion]
    assert all(cameras[i] != cameras[i - 1] for i in range(1, len(cameras)))


def test_hook_pushes_in_and_resolution_settles() -> None:
    motion = direct_clip_motion(8, seed=7)
    assert motion[0].camera == CameraMotion.ken_burns
    assert motion[0].intensity >= 0.66
    assert motion[-1].camera in (CameraMotion.zoom_out, CameraMotion.hold)
    assert motion[-1].intensity <= 0.52


def test_deterministic_for_same_seed() -> None:
    a = direct_clip_motion(11, seed=99, emotions=[None] * 11)
    b = direct_clip_motion(11, seed=99, emotions=[None] * 11)
    assert a == b


def test_varies_across_seeds() -> None:
    a = [m.camera for m in direct_clip_motion(11, seed=1, emotions=[None] * 11)]
    b = [m.camera for m in direct_clip_motion(11, seed=2, emotions=[None] * 11)]
    assert a != b


def test_busy_cameras_never_back_to_back() -> None:
    motion = direct_clip_motion(20, seed=123, emotions=[None] * 20)
    cams = [m.camera for m in motion]
    for i in range(1, len(cams)):
        assert not (cams[i] in _BUSY and cams[i - 1] in _BUSY)


def test_intensity_within_envelope() -> None:
    motion = direct_clip_motion(15, seed=5, emotions=[None] * 15)
    assert all(0.40 <= m.intensity <= 0.75 for m in motion)


def test_emotion_bias_does_not_break_invariants() -> None:
    emotions: list[EmotionType | None] = [
        EmotionType.hook,
        EmotionType.tense_buildup,
        EmotionType.shock,
        EmotionType.reflective,
        EmotionType.climactic,
        EmotionType.tragic,
        EmotionType.reflective,
    ]
    motion = direct_clip_motion(len(emotions), seed=3, emotions=emotions)
    assert all(isinstance(m, ClipMotion) for m in motion)
    cams = [m.camera for m in motion]
    assert all(cams[i] != cams[i - 1] for i in range(1, len(cams)))
