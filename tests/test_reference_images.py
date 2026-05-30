"""Tests for topic-creator reference image resolution."""

from pathlib import Path

from shorts_pipeline.image_worker.reference_images import (
    normalize_figure_key_loose,
    resolve_person_references,
)


def test_normalize_figure_key_loose_strips_punctuation() -> None:
    assert normalize_figure_key_loose("Isaac Newton,") == normalize_figure_key_loose("isaac newton")


def test_resolve_isaac_newton_from_topic_creator() -> None:
    root = Path(r"C:\Users\35383\Documents\topic creator\output\famous_people_1000")
    if not root.is_dir():
        return
    refs = resolve_person_references("Isaac Newton", root=root)
    assert refs is not None
    assert len(refs.image_paths) == 4
    assert all(p.is_file() for p in refs.image_paths)


def test_resolve_unknown_figure_returns_none(tmp_path: Path) -> None:
    batch = tmp_path / "batch_001"
    img_dir = batch / "images"
    img_dir.mkdir(parents=True)
    tsv = batch / "subjects.tsv"
    tsv.write_text(
        "index\twikidata_id\tlabel\timage_url_1\timage_url_2\timage_url_3\timage_url_4\n"
        "1\tQ1\tAlice\thttp://a/1\thttp://a/2\thttp://a/3\thttp://a/4\n",
        encoding="utf-8",
    )
    for slot in range(1, 5):
        (img_dir / f"00001_Q1_{slot}.png").write_bytes(b"x")
    refs = resolve_person_references("Alice", root=tmp_path)
    assert refs is not None
    assert refs.wikidata_id == "Q1"
