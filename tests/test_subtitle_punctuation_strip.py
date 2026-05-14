"""Unit tests for subtitle token cleaning (leading comma / Unicode punct)."""

from __future__ import annotations

import pytest

from shorts_pipeline.aligner.ass import (
    _strip_leading_commas_subtitle_line,
    _strip_outer_punctuation,
    _subtitle_display,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        (",ONLY", "ONLY"),
        ("\uFF0cONLY", "ONLY"),
        (", only", "only"),
        ('"Hello"', "Hello"),
        ("shield,", "shield"),
        ("…wait", "wait"),
        ("(,word", "word"),
        ("((Hi", "Hi"),
    ],
)
def test_strip_outer_punctuation_leading_trailing(raw: str, expected: str) -> None:
    assert _strip_outer_punctuation(raw) == expected


def test_strip_preserves_contraction_apostrophe() -> None:
    assert _strip_outer_punctuation("it's") == "it's"
    assert _strip_outer_punctuation("boys'") == "boys'"


def test_subtitle_display_upper() -> None:
    assert _subtitle_display(",boy", use_upper=True) == "BOY"
    assert _subtitle_display("(,run", use_upper=True) == "RUN"


def test_subtitle_display_suppresses_punctuation_only_tokens() -> None:
    assert _subtitle_display(",", use_upper=False) == ""
    assert _subtitle_display(",", use_upper=True) == ""
    assert _subtitle_display("…", use_upper=False) == ""
    assert _subtitle_display(",,,", use_upper=True) == ""


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("{\\fad(1,2)},,HELLO", "{\\fad(1,2)}HELLO"),
        (",,{\\an5}WORD", "{\\an5}WORD"),
        ("  ,，  ,visible", "visible"),
        ("{\\1c&}a, b", "{\\1c&}a, b"),
        ("{\\fad(1,2)}(,HELLO", "{\\fad(1,2)}HELLO"),
        ("{\\an5}(,WORD", "{\\an5}WORD"),
    ],
)
def test_strip_leading_commas_subtitle_line(raw: str, expected: str) -> None:
    assert _strip_leading_commas_subtitle_line(raw) == expected
