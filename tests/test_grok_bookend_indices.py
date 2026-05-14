from __future__ import annotations

import pytest

from shorts_pipeline.image_worker.grok_bookend_generator import grok_clause_indices
from shorts_pipeline.image_worker.triple_hybrid_image_generator import split_grok_flux_local_indices


def test_grok_indices_first_last_only() -> None:
    assert grok_clause_indices(14, None) == {0, 13}
    assert grok_clause_indices(12, "") == {0, 11}
    assert grok_clause_indices(1, None) == {0}  # first == last


def test_grok_indices_with_extras() -> None:
    assert grok_clause_indices(14, "6, 7") == {0, 6, 7, 13}


def test_grok_indices_extra_out_of_range() -> None:
    with pytest.raises(ValueError, match="out of range"):
        grok_clause_indices(5, "9")


def test_split_triple_hybrid_14_clauses_default_grok() -> None:
    grok, flux, local = split_grok_flux_local_indices(14, None)
    assert grok == {0, 13}
    assert flux == {2, 4, 6, 8, 10}
    assert local == {1, 3, 5, 7, 9, 11, 12}
    assert grok | flux | local == set(range(14))


def test_split_triple_hybrid_extra_grok_steals_from_flux() -> None:
    grok, flux, local = split_grok_flux_local_indices(14, "6")
    assert grok == {0, 6, 13}
    assert flux == {2, 4, 8, 10}
    assert local == {1, 3, 5, 7, 9, 11, 12}
