"""Calibration runner.

For each niche in ``calibration_topics.json``:
  1. Generate a plan.json with the niche's prompt (Gemini -> DeepSeek fallback).
  2. Render narration.wav via Kokoro at speed=1.0.
  3. Append a row to ``calibration_results.csv`` with text features + duration.

Restartable: skips any (niche, topic) that already has plan.json + narration.wav
under ``data/calibration/<niche>/<NN_slug>/``.

Usage:
    python run_calibration.py                # all niches, all topics
    python run_calibration.py --niche history --niche science
    python run_calibration.py --topics-per-niche 2
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from shorts_pipeline.config.settings import Settings  # noqa: E402
from shorts_pipeline.tts_worker.kokoro import KokoroTTSClient  # noqa: E402

# Reuse helpers from prep_jobs / make_scripts where practical.
import importlib

_PREP = importlib.import_module("prep_jobs")
_CAL = importlib.import_module("calibrate_tts")

OUT_ROOT = ROOT / "data" / "calibration"
CSV_PATH = ROOT / "calibration_results.csv"
TOPICS_PATH = ROOT / "calibration_topics.json"

_SLUG = re.compile(r"[^a-z0-9]+")


def slug(s: str, max_len: int = 60) -> str:
    out = _SLUG.sub("-", s.lower()).strip("-")
    return out[:max_len] or "topic"


def already_done(job_dir: Path) -> bool:
    return (job_dir / "plan.json").is_file() and _PREP._wav_ok(job_dir / "narration.wav")


def load_topics(niches_filter: list[str], per_niche: int) -> list[tuple[str, int, str]]:
    data = json.loads(TOPICS_PATH.read_text(encoding="utf-8"))
    out = []
    for niche, topics in data.items():
        if niche.startswith("_"):
            continue
        if niches_filter and niche not in niches_filter:
            continue
        for i, t in enumerate(topics[:per_niche], 1):
            out.append((niche, i, t))
    return out


def write_csv_header_if_new() -> None:
    if CSV_PATH.exists():
        return
    with CSV_PATH.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "niche", "idx", "topic", "job_dir",
            "words", "syllables", "avg_syllables", "polysyl_count", "polysyl_ratio",
            "proper_noun_count", "proper_noun_ratio", "terminators", "very_long_words",
            "duration", "wps", "sps",
        ])


def append_csv(niche: str, idx: int, topic: str, job_dir: Path, feats: dict, dur: float) -> None:
    write_csv_header_if_new()
    with CSV_PATH.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            niche, idx, topic, str(job_dir.relative_to(ROOT)),
            feats["words"], feats["syllables"], f"{feats['avg_syllables']:.3f}",
            feats["polysyl_count"], f"{feats['polysyl_ratio']:.3f}",
            feats["proper_noun_count"], f"{feats['proper_noun_ratio']:.3f}",
            feats["terminators"], feats["very_long_words"],
            f"{dur:.2f}", f"{feats['words']/dur:.3f}", f"{feats['syllables']/dur:.3f}",
        ])


def already_in_csv(niche: str, idx: int) -> bool:
    if not CSV_PATH.exists():
        return False
    with CSV_PATH.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["niche"] == niche and int(row["idx"]) == idx:
                return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--niche", action="append", default=[], help="restrict to one niche (repeatable)")
    ap.add_argument("--topics-per-niche", type=int, default=3)
    args = ap.parse_args()

    work = load_topics(args.niche, args.topics_per_niche)
    print(f"calibration: {len(work)} jobs across {len({n for n,_,_ in work})} niches")
    OUT_ROOT.mkdir(parents=True, exist_ok=True)

    settings = Settings()
    tts = KokoroTTSClient(settings)

    for niche, idx, topic in work:
        niche_dir = OUT_ROOT / niche
        niche_dir.mkdir(parents=True, exist_ok=True)
        job_dir = niche_dir / f"{idx:02d}_{slug(topic)}"
        job_dir.mkdir(parents=True, exist_ok=True)

        plan_path = job_dir / "plan.json"
        wav_path = job_dir / "narration.wav"

        if already_done(job_dir) and already_in_csv(niche, idx):
            print(f"[{niche:>14s} {idx}] skip (done + csv): {topic}")
            continue

        print(f"[{niche:>14s} {idx}] {topic}")
        t0 = time.time()
        try:
            if not plan_path.is_file():
                niche_sys, niche_user_fn = _PREP.load_niche(niche)
                plan_dict = _PREP.generate_plan_for_topic(settings, niche_sys, niche_user_fn, topic, niche=niche)
                plan_path.write_text(json.dumps(plan_dict, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"    plan ok ({len(plan_dict.get('full_script','').split())} words, "
                      f"{time.time()-t0:.0f}s)")
            else:
                plan_dict = json.loads(plan_path.read_text(encoding="utf-8"))
                print("    plan reused")

            if not _PREP._wav_ok(wav_path):
                t1 = time.time()
                tts.synthesize_wav((plan_dict.get("full_script") or "").strip(), wav_path)
                print(f"    tts  ok ({time.time()-t1:.0f}s)")
            else:
                print("    tts  reused")

            text = (plan_dict.get("full_script") or "").strip()
            feats = _CAL.analyse_text(text)
            dur = _CAL.probe_duration(wav_path)
            append_csv(niche, idx, topic, job_dir, feats, dur)
            wps = feats["words"] / dur
            sps = feats["syllables"] / dur
            print(f"    words={feats['words']} syl={feats['syllables']} dur={dur:.1f}s wps={wps:.2f} sps={sps:.2f}")
        except Exception as e:
            print(f"    FAILED: {e}", file=sys.stderr)
            traceback.print_exc(limit=2)
            continue

    print("\ndone.")
    if CSV_PATH.exists():
        print(f"results: {CSV_PATH}")


if __name__ == "__main__":
    main()
