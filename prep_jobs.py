"""Batch prep: LLM script (plan.json) + TTS (narration.wav) into job folders — no SQLite, no images, no render.

Creates one folder per person directly under ``data/jobs/``, same naming as the Web UI pipeline:
  ``adolf-hitler-22605440/``  (slug + 8-char id)

Each folder contains:
  - plan.json      — validated NarrationPlan (same rules as make_scripts.py)
  - narration.wav  — TTS of full_script (.env SHORTS_TTS_BACKEND)

Does not register jobs in the database. Use your merge/render step or Web UI reconcile later.

Usage:
    python prep_jobs.py history "C:/Users/35383/Documents/topic creator/output/famous_people_1000/batch_001"
    python prep_jobs.py history .../batch_001 --limit 3
    python prep_jobs.py history .../batch_001 --tts-only          # plan.json already on disk
    python prep_jobs.py history .../batch_001 --script-only       # skip TTS

    # all 10 batches:
    # 1..10 | % { python prep_jobs.py history "...\famous_people_1000\batch_$('{0:D3}' -f $_)" }
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import unicodedata
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from shorts_pipeline.config.settings import Settings  # noqa: E402
from shorts_pipeline.planner.schema import NarrationPlan  # noqa: E402

# Reuse topic loading + LLM generation from make_scripts.py
from make_scripts import (  # noqa: E402
    _build_topic_prompt,
    _slugify,
    generate_plan_for_topic,
    load_niche,
    load_topics,
)

_FOLDER_IDX_RE = re.compile(r"^\d+_(.+)$")
_SLUG_RE = re.compile(r"[^a-z0-9]+")
_JOB_SUFFIX_RE = re.compile(r"-[0-9a-f]{8}$")


def normalize_figure_key(name: str) -> str:
    """Match topic-creator overlap logic (casefold + NFKC + collapsed spaces)."""
    s = unicodedata.normalize("NFKC", name or "")
    s = " ".join(s.split())
    return s.casefold()


def normalize_figure_key_loose(name: str) -> str:
    s = normalize_figure_key(name)
    s = s.replace(",", " ").replace(".", " ")
    return " ".join(s.split())


def _figure_slug(figure_name: str) -> str:
    """Same slug rule as JobStore.create_job."""
    return _SLUG_RE.sub("-", figure_name.lower()).strip("-")[:32] or "topic"


def make_job_folder_id(figure_name: str, *, random_suffix: bool = False) -> str:
    """Pipeline-style folder id: ``henry-ford-d3046068``."""
    slug = _figure_slug(figure_name)
    if random_suffix:
        short = uuid.uuid4().hex[:8]
    else:
        short = hashlib.sha256(normalize_figure_key_loose(figure_name).encode("utf-8")).hexdigest()[:8]
    return f"{slug}-{short}"


def _label_from_job_folder(folder_name: str) -> str:
    """Derive display name from folder (pipeline id, legacy batch id, or plain slug)."""
    m = _FOLDER_IDX_RE.match(folder_name)
    if m:
        raw = m.group(1)
    else:
        raw = _JOB_SUFFIX_RE.sub("", folder_name) if _JOB_SUFFIX_RE.search(folder_name) else folder_name
    return raw.replace("-", " ").strip()


def resolve_job_dir(
    jobs_root: Path,
    figure_name: str,
    overlap_index: FigureOverlapIndex,
    *,
    allow_overlap: bool,
) -> Path:
    """Pick existing folder for this figure, or allocate a new pipeline-style id."""
    if not allow_overlap:
        existing = overlap_index.find(figure_name)
        if existing is not None:
            return existing

    slug = _figure_slug(figure_name)
    matches = sorted(
        (p for p in jobs_root.iterdir() if p.is_dir() and p.name.startswith(f"{slug}-")),
        key=lambda p: p.name,
    )
    with_plan = [p for p in matches if (p / "plan.json").is_file()]
    if len(with_plan) == 1:
        return with_plan[0]
    if len(matches) == 1:
        return matches[0]

    return jobs_root / make_job_folder_id(figure_name, random_suffix=allow_overlap)


def _figure_keys_for_label(label: str) -> set[str]:
    label = (label or "").strip()
    if not label:
        return set()
    return {normalize_figure_key(label), normalize_figure_key_loose(label)}


class FigureOverlapIndex:
    """Maps normalized figure keys → existing job folder (any batch under jobs_root)."""

    def __init__(self) -> None:
        self._by_key: dict[str, Path] = {}

    def scan_jobs_root(self, jobs_root: Path) -> int:
        """Load historical_figure (or folder name) from every plan.json on disk."""
        n = 0
        if not jobs_root.is_dir():
            return 0
        for plan_path in jobs_root.glob("**/plan.json"):
            job_dir = plan_path.parent
            label = ""
            try:
                data = json.loads(plan_path.read_text(encoding="utf-8"))
                if isinstance(data.get("historical_figure"), str):
                    label = data["historical_figure"].strip()
            except Exception:
                pass
            if not label:
                label = _label_from_job_folder(job_dir.name)
            for key in _figure_keys_for_label(label):
                if key not in self._by_key:
                    self._by_key[key] = job_dir
            n += 1
        return n

    def find(self, label: str) -> Path | None:
        for key in _figure_keys_for_label(label):
            hit = self._by_key.get(key)
            if hit is not None:
                return hit
        return None

    def register(self, label: str, job_dir: Path) -> None:
        for key in _figure_keys_for_label(label):
            self._by_key[key] = job_dir


def _synthesize_tts(settings: Settings, full_script: str, out_path: Path) -> None:
    backend = settings.tts_backend.lower().strip()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if backend in ("kokoro", "kokoro_http"):
        from shorts_pipeline.tts_worker.kokoro import KokoroTTSClient

        KokoroTTSClient(settings).synthesize_wav(full_script, out_path)
    else:
        raise RuntimeError(
            f"Unknown tts_backend {backend!r}. "
            "Set SHORTS_TTS_BACKEND to kokoro or kokoro_http."
        )


def _wav_ok(path: Path, *, min_bytes: int = 4096) -> bool:
    return path.is_file() and path.stat().st_size >= min_bytes


def _load_plan_dict(path: Path, settings: Settings) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    use_figure_name = getattr(settings, "image_prompts_include_figure_name", False)
    plan = NarrationPlan.model_validate(data, context={"allow_figure_name": use_figure_name})
    return plan.model_dump(mode="json")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("niche", help="niche module name, e.g. history")
    ap.add_argument("batch_path", help="topics.txt folder or .txt file (topic creator format)")
    ap.add_argument(
        "--jobs-root",
        default="",
        help="override jobs root (default: <SHORTS_DATA_DIR>/jobs)",
    )
    ap.add_argument("--limit", type=int, default=0, help="stop after N topics (0 = all)")
    ap.add_argument("--start", type=int, default=1, help="skip topics with index < START")
    ap.add_argument("--mode", choices=["normal", "long"], default="normal")
    ap.add_argument(
        "--script-only",
        action="store_true",
        help="write plan.json only, skip TTS",
    )
    ap.add_argument(
        "--tts-only",
        action="store_true",
        help="require plan.json on disk, only run TTS",
    )
    ap.add_argument(
        "--mirror-scripts-out",
        action="store_true",
        help="also save a copy under scripts_out/<niche>/<batch>/",
    )
    ap.add_argument(
        "--allow-overlap",
        action="store_true",
        help="allow the same person in multiple job folders (default: skip global duplicates)",
    )
    args = ap.parse_args()

    if args.script_only and args.tts_only:
        print("cannot use --script-only and --tts-only together", file=sys.stderr)
        return 2

    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "jobs").mkdir(parents=True, exist_ok=True)

    batch_path = Path(args.batch_path)
    topics = load_topics(batch_path)
    if not topics:
        print(f"no topics in {batch_path}", file=sys.stderr)
        return 1

    batch_name = batch_path.stem if batch_path.is_file() else batch_path.name
    jobs_root = Path(args.jobs_root) if args.jobs_root else (settings.data_dir / "jobs")
    jobs_root.mkdir(parents=True, exist_ok=True)

    mirror_root: Path | None = None
    if args.mirror_scripts_out:
        mirror_root = ROOT / "scripts_out" / args.niche / batch_name
        mirror_root.mkdir(parents=True, exist_ok=True)

    niche_sys, niche_user_fn = load_niche(args.niche)
    do_script = not args.tts_only
    do_tts = not args.script_only

    overlap_index = FigureOverlapIndex()
    indexed = overlap_index.scan_jobs_root(jobs_root)
    batch_seen: dict[str, int] = {}

    print(
        f"niche={args.niche}  batch={batch_name}  topics={len(topics)}  "
        f"jobs={jobs_root}  tts={settings.tts_backend}  "
        f"script={'yes' if do_script else 'skip'}  tts_run={'yes' if do_tts else 'skip'}  "
        f"overlap_guard={'off' if args.allow_overlap else f'on ({indexed} plans indexed)'}"
    )

    done = 0
    skipped_overlap = 0
    for idx, topic in topics:
        if idx < args.start:
            continue

        # Topic can be a plain string or a rich dict (from niche_progress batches).
        # Use the title for slug/key/display; pass the richer prompt to the planner.
        if isinstance(topic, dict):
            topic_text = topic["title"]
            topic_for_prompt = _build_topic_prompt(topic)
        else:
            topic_text = topic
            topic_for_prompt = topic

        archive_name = f"{idx:03d}_{_slugify(topic_text)}"

        if not args.allow_overlap:
            loose = normalize_figure_key_loose(topic_text)
            if loose in batch_seen:
                print(
                    f"[{idx:03d}] skip (duplicate in this batch, line {batch_seen[loose]}): "
                    f"{topic_text[:60]}"
                )
                skipped_overlap += 1
                continue
            batch_seen[loose] = idx

        job_dir = resolve_job_dir(
            jobs_root, topic_text, overlap_index, allow_overlap=args.allow_overlap
        )
        plan_path = job_dir / "plan.json"
        wav_path = job_dir / "narration.wav"

        if (
            not args.allow_overlap
            and overlap_index.find(topic_text) is not None
            and do_script
            and do_tts
            and plan_path.is_file()
            and _wav_ok(wav_path)
        ):
            rel = (
                job_dir.relative_to(jobs_root)
                if job_dir.is_relative_to(jobs_root)
                else job_dir
            )
            print(f"[{idx:03d}] skip (already prepared): {topic_text[:50]}  ->  {rel}")
            skipped_overlap += 1
            continue

        if do_script and do_tts and plan_path.is_file() and _wav_ok(wav_path):
            print(f"[{idx:03d}] skip (plan + wav): {topic_text[:70]}")
            if not args.allow_overlap:
                overlap_index.register(topic_text, job_dir)
            continue
        if args.tts_only and not plan_path.is_file():
            print(f"[{idx:03d}] skip (no plan.json): {topic_text[:70]}", file=sys.stderr)
            continue

        print(f"[{idx:03d}] {topic_text[:70]}  ->  {job_dir.name}/")
        t0 = time.time()
        job_dir.mkdir(parents=True, exist_ok=True)

        try:
            if do_script:
                if plan_path.is_file():
                    print("  plan: reuse existing plan.json")
                    plan_dict = _load_plan_dict(plan_path, settings)
                else:
                    print("  plan: generating…")
                    plan_dict = generate_plan_for_topic(settings, niche_sys, niche_user_fn, topic_for_prompt, niche=args.niche)
                    plan_dict["video_mode"] = args.mode
                    plan_dict["niche"] = args.niche
                    plan_path.write_text(
                        json.dumps(plan_dict, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    if mirror_root is not None:
                        mirror = mirror_root / f"{archive_name}.json"
                        mirror.write_text(plan_path.read_text(encoding="utf-8"), encoding="utf-8")
                    print(f"  plan: ok ({len(plan_dict.get('full_script', '').split())} words)")
            else:
                plan_dict = _load_plan_dict(plan_path, settings)

            if do_tts:
                if _wav_ok(wav_path):
                    print("  tts: skip (narration.wav exists)")
                else:
                    script = (plan_dict.get("full_script") or "").strip()
                    if not script:
                        raise RuntimeError("plan has no full_script for TTS")
                    print(f"  tts: synthesizing ({len(script.split())} words)…")
                    _synthesize_tts(settings, script, wav_path)
                    print(f"  tts: ok -> {wav_path.name} ({wav_path.stat().st_size // 1024} KB)")

        except Exception as e:
            print(f"  FAILED: {e}", file=sys.stderr)
            continue

        if not args.allow_overlap:
            fig = (plan_dict.get("historical_figure") or topic_text).strip()
            overlap_index.register(fig, job_dir)

        print(f"  done ({time.time() - t0:.1f}s)")
        done += 1
        if args.limit and done >= args.limit:
            break

    print(
        f"\nfinished — {done} folder(s) under {jobs_root}"
        + (f", {skipped_overlap} skipped (overlap)" if skipped_overlap else "")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
