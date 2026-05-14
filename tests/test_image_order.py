"""Ordering of clause PNG artifacts by numeric index in filename."""

from __future__ import annotations

from dataclasses import dataclass

from shorts_pipeline.jobs.image_order import clause_index_from_image_path, sort_clause_png_artifacts


@dataclass
class _FakeArt:
    path: str


def test_clause_index_from_image_path() -> None:
    assert clause_index_from_image_path(r"C:\job\clause_013.png") == 13
    assert clause_index_from_image_path("clause_2.PNG") == 2
    assert clause_index_from_image_path("other.png") == 10**9


def test_sort_clause_png_artifacts_numeric_not_lexicographic() -> None:
    arts = [
        _FakeArt("clause_010.png"),
        _FakeArt("clause_2.png"),
        _FakeArt("clause_001.png"),
    ]
    out = sort_clause_png_artifacts(arts)
    assert [a.path for a in out] == ["clause_001.png", "clause_2.png", "clause_010.png"]
