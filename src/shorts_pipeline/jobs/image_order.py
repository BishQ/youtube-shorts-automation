"""Stable ordering for clause image artifacts (DB order / string sort is wrong)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol

_CLAUSE_PNG_RE = re.compile(r"clause_(\d+)\.(?:png|jpe?g|webp)$", re.IGNORECASE)
_CLAUSE_MP4_RE = re.compile(r"clause_(\d+)\.mp4$", re.IGNORECASE)


class _HasPath(Protocol):
    path: str


def clause_index_from_image_path(path: str) -> int:
    """Return 0-based clause index from filename, or a large sentinel if unknown."""
    name = Path(path).name
    m = _CLAUSE_PNG_RE.search(name)
    return int(m.group(1)) if m else 10**9


def clause_index_from_video_path(path: str) -> int:
    name = Path(path).name
    m = _CLAUSE_MP4_RE.search(name)
    return int(m.group(1)) if m else 10**9


def sort_clause_mp4_artifacts(artifacts: list[_HasPath]) -> list[_HasPath]:
    return sorted(artifacts, key=lambda a: clause_index_from_video_path(a.path))


def sort_clause_png_artifacts(artifacts: list[_HasPath]) -> list[_HasPath]:
    """Sort by ``clause_NNN`` index so ``clause_2`` comes before ``clause_010``."""
    return sorted(artifacts, key=lambda a: clause_index_from_image_path(a.path))
