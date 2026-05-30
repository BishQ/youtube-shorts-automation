"""Resolve per-person reference photos from topic-creator famous_people batches."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from shorts_pipeline.logging_setup import get_logger

log = get_logger(__name__)

_IMAGES_PER_PERSON = 4
_QID_RE = re.compile(r"^Q\d+$", re.I)
_IMG_RE = re.compile(
    r"^(\d{5})_(Q\d+)_(\d+)\.(jpg|jpeg|png|webp|gif|tif|bmp)$",
    re.I,
)
_IMG_IDX_ONLY_RE = re.compile(
    r"^(\d{5})_(\d+)\.(jpg|jpeg|png|webp|gif|tif|bmp)$",
    re.I,
)


@dataclass(frozen=True)
class PersonReferences:
    """Four real reference photos for one famous person."""

    figure_name: str
    label: str
    index: int
    wikidata_id: str
    batch_name: str
    image_paths: tuple[Path, Path, Path, Path]

    def lora_stem(self) -> str:
        if self.wikidata_id:
            return self.wikidata_id
        return f"{self.index:05d}"


def normalize_figure_key(name: str) -> str:
    s = unicodedata.normalize("NFKC", name or "")
    s = " ".join(s.split())
    return s.casefold()


def normalize_figure_key_loose(name: str) -> str:
    s = normalize_figure_key(name)
    s = s.replace(",", " ").replace(".", " ")
    return " ".join(s.split())


def _parse_subjects_tsv(tsv: Path) -> list[tuple[int, str, str]]:
    rows: list[tuple[int, str, str]] = []
    for i, raw in enumerate(tsv.read_text(encoding="utf-8").splitlines()):
        if not raw.strip():
            continue
        parts = raw.split("\t")
        if i == 0 and parts and parts[0].lower() == "index":
            continue
        if len(parts) < 3:
            continue
        try:
            idx = int(parts[0])
        except ValueError:
            continue
        rows.append((idx, parts[1].strip(), parts[2].strip()))
    return rows


def _index_images(img_dir: Path) -> dict[tuple[int, str | None], dict[int, Path]]:
    """Map (index, qid|None) -> {slot: path}. Supports QID and index-only filenames."""
    out: dict[tuple[int, str | None], dict[int, Path]] = {}
    if not img_dir.is_dir():
        return out
    for p in img_dir.iterdir():
        if not p.is_file():
            continue
        m = _IMG_RE.match(p.name)
        if m:
            key = (int(m.group(1)), m.group(2).upper())
            out.setdefault(key, {})[int(m.group(3))] = p
            continue
        m2 = _IMG_IDX_ONLY_RE.match(p.name)
        if m2:
            key = (int(m2.group(1)), None)
            out.setdefault(key, {})[int(m2.group(2))] = p
    return out


def _ordered_four(slots: dict[int, Path]) -> tuple[Path, Path, Path, Path] | None:
    if all(k in slots for k in (1, 2, 3, 4)):
        return (slots[1], slots[2], slots[3], slots[4])
    return None


def _match_label(query: str, candidate: str) -> bool:
    q = normalize_figure_key_loose(query)
    c = normalize_figure_key_loose(candidate)
    if not q or not c:
        return False
    if q == c:
        return True
    if q in c or c in q:
        return True
    q_tokens = set(q.split())
    c_tokens = set(c.split())
    if len(q_tokens) >= 2 and q_tokens <= c_tokens:
        return True
    if len(c_tokens) >= 2 and c_tokens <= q_tokens:
        return True
    return False


def _count_batches_with_images(root: Path) -> tuple[int, int]:
    """Return (batches with subjects.tsv, batches with at least one image file)."""
    with_tsv = 0
    with_images = 0
    for batch in root.glob("batch_*"):
        if not (batch / "subjects.tsv").is_file():
            continue
        with_tsv += 1
        img_dir = batch / "images"
        if img_dir.is_dir() and any(p.is_file() for p in img_dir.iterdir()):
            with_images += 1
    return with_tsv, with_images


def resolve_person_references(
    figure_name: str,
    *,
    root: Path,
) -> PersonReferences | None:
    """Find 4 reference images for *figure_name* under *root*/batch_*/."""
    if not figure_name.strip():
        return None
    if not root.is_dir():
        log.warning("reference_images_root_missing", root=str(root))
        return None

    for batch in sorted(root.glob("batch_*")):
        tsv = batch / "subjects.tsv"
        img_dir = batch / "images"
        if not tsv.is_file() or not img_dir.is_dir():
            continue
        by_key = _index_images(img_dir)
        for idx, qid, label in _parse_subjects_tsv(tsv):
            if not _match_label(figure_name, label):
                continue
            qid_norm = qid.upper() if _QID_RE.match(qid) else ""
            paths = None
            if qid_norm:
                paths = _ordered_four(by_key.get((idx, qid_norm), {}))
            if paths is None:
                paths = _ordered_four(by_key.get((idx, None), {}))
            if paths is None:
                log.warning(
                    "reference_images_incomplete",
                    figure=figure_name,
                    batch=batch.name,
                    index=idx,
                    qid=qid_norm or qid,
                )
                continue
            log.info(
                "reference_images_resolved",
                figure=figure_name,
                label=label,
                batch=batch.name,
                index=idx,
                qid=qid_norm,
            )
            return PersonReferences(
                figure_name=figure_name.strip(),
                label=label,
                index=idx,
                wikidata_id=qid_norm,
                batch_name=batch.name,
                image_paths=paths,
            )
    batches_tsv, batches_images = _count_batches_with_images(root)
    if batches_tsv and not batches_images:
        log.warning(
            "reference_images_not_downloaded",
            figure=figure_name,
            root=str(root),
            hint="Run: python download_subject_images.py --root <famous_people_1000> --all-batches",
        )
    else:
        log.warning("reference_images_not_found", figure=figure_name, root=str(root))
    return None
