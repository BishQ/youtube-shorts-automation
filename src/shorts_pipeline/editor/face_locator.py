"""Detect face position in an image and return a subtitle-safe zone (Rule 12)."""

from __future__ import annotations

from pathlib import Path

from shorts_pipeline.planner.schema import SubtitlePosition


def detect_subtitle_position(image_path: Path) -> SubtitlePosition:
    """
    Run MediaPipe face detection on image_path and return the subtitle position
    that avoids covering the face.  Falls back to bottom if MediaPipe is
    unavailable or no face is found.
    """
    try:
        import cv2  # type: ignore
        import mediapipe as mp  # type: ignore
    except ImportError:
        return SubtitlePosition.bottom

    try:
        img = cv2.imread(str(image_path))
        if img is None:
            return SubtitlePosition.bottom

        mp_face = mp.solutions.face_detection
        with mp_face.FaceDetection(model_selection=1, min_detection_confidence=0.4) as fd:
            rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            results = fd.process(rgb)

        if not results.detections:
            return SubtitlePosition.bottom

        # Bounding box of all detected faces (relative coords 0-1)
        min_y = 1.0
        max_y = 0.0
        for det in results.detections:
            bb = det.location_data.relative_bounding_box
            min_y = min(min_y, bb.ymin)
            max_y = max(max_y, bb.ymin + bb.height)

        if max_y < 0.4:
            return SubtitlePosition.bottom   # face in top third → subs at bottom
        if min_y > 0.6:
            return SubtitlePosition.top      # face in bottom third → subs at top
        return SubtitlePosition.bottom       # face in middle → bottom is safer
    except Exception:  # noqa: BLE001
        return SubtitlePosition.bottom
