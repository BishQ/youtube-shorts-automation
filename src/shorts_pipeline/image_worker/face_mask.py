"""Build soft face inpaint masks for Flux ControlNet refine pass."""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter


def _ellipse_mask(width: int, height: int) -> Image.Image:
    """Portrait fallback: ellipse over upper-center (typical talking-head framing)."""
    mask = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(mask)
    cx = width // 2
    cy = int(height * 0.28)
    rx = int(width * 0.22)
    ry = int(height * 0.16)
    draw.ellipse((cx - rx, cy - ry, cx + rx, cy + ry), fill=255)
    return mask.filter(ImageFilter.GaussianBlur(radius=max(8, width // 64)))


def _insightface_mask(image_path: Path) -> Image.Image | None:
    try:
        import cv2  # noqa: PLC0415
        from insightface.app import FaceAnalysis  # noqa: PLC0415
    except ImportError:
        return None

    img_bgr = cv2.imread(str(image_path))
    if img_bgr is None:
        return None
    h, w = img_bgr.shape[:2]
    app = FaceAnalysis(name="antelopev2", providers=["CPUExecutionProvider"])
    app.prepare(ctx_id=0, det_size=(640, 640))
    faces = app.get(img_bgr)
    if not faces:
        return None
    face = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
    x1, y1, x2, y2 = [int(v) for v in face.bbox]
    margin = int(max(x2 - x1, y2 - y1) * 0.35)
    x1, y1 = max(0, x1 - margin), max(0, y1 - margin)
    x2, y2 = min(w, x2 + margin), min(h, y2 + margin)
    mask = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((x1, y1, x2, y2), fill=255)
    return mask.filter(ImageFilter.GaussianBlur(radius=max(6, w // 80)))


def build_face_inpaint_mask(reference_image: Path, *, width: int, height: int) -> Path:
    """Return path to a grayscale PNG (white = inpaint region)."""
    mask = _insightface_mask(reference_image)
    if mask is None:
        mask = _ellipse_mask(width, height)
    else:
        if mask.size != (width, height):
            mask = mask.resize((width, height), Image.Resampling.LANCZOS)

    out = Path(tempfile.gettempdir()) / f"face_inpaint_mask_{reference_image.stem}.png"
    mask.save(out)
    return out
