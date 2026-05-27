"""Volume test — Gemma 4 Q4 local, 10 generations per niche.

17 niches × 10 scripts = 170 total. Uses MAIN architecture with all the
post-processing upgrades (strip_stray_questions, smart cadence rewrite).
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

_env = ROOT / ".env"
if _env.is_file():
    for line in _env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ: os.environ[k] = v

from shorts_pipeline.config.settings import Settings
from shorts_pipeline.planner.cinematic import ARCHITECTURES, with_retries
from shorts_pipeline.planner.niche_caps import caps_for, count_syllables
import bench_lmstudio_v2 as bv2

NICHES = [
    "business", "cosmic", "crime", "cults", "edutainment", "facts",
    "health", "history", "lost_tech", "military", "mythology",
    "psychology", "science", "sports", "survival", "tech_hackers", "wealth",
]
SCRIPTS_PER_NICHE = 5
ARCH = "MAIN"

# Test BOTH Gemma 4 quantizations
COMPETITORS = [
    ("gemma-q4", "gemma-4-e4b"),         # Q4_K_M, 4.97 GB — focus on this
    # ("gemma-q6", "google/gemma-4-e4b"),  # Q6_K, 6.71 GB — skipped (3x slower)
]
BASE_URL = "http://127.0.0.1:1234/v1"
CTX = 12288
PER_REQUEST_TIMEOUT_S = 600.0
MAX_RETRIES = 3

OUT = ROOT / "scripts_out" / "gemma_volume"
OUT.mkdir(parents=True, exist_ok=True)


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60]


def _load_topics(niche: str, n: int) -> list[str]:
    """Load first N distinct topics from this niche's batch_*.json files."""
    folder = ROOT / "topics" / "niches" / niche
    out: list[str] = []
    for bp in sorted(folder.glob("batch_*.json")):
        try:
            arr = json.loads(bp.read_text(encoding="utf-8"))
        except Exception: continue
        for it in arr if isinstance(arr, list) else []:
            if isinstance(it, dict):
                title = (it.get("title") or "").strip()
                if title and title not in out:
                    out.append(title)
                    if len(out) >= n:
                        return out
    return out


CSV_COLS = ["competitor", "model", "niche", "idx", "topic", "success", "attempts_used",
            "total_time_s", "words", "syllables", "min_w", "max_w",
            "in_band", "error_tags", "error"]


def _classify_error(err: str) -> list[str]:
    tags = []
    for m in re.finditer(r"Value error, ([^|\n]+)", err):
        text = m.group(1)
        if "is only" in text and "minimum is" in text:
            mm = re.search(r"only (\d+) words.*minimum is (\d+)", text)
            tags.append(f"SHORT_{mm.group(1)}<{mm.group(2)}" if mm else "SHORT")
        elif "exceeds" in text and "tolerance" in text:
            mm = re.search(r"is (\d+) words", text)
            tags.append(f"LONG_{mm.group(1)}" if mm else "LONG")
        elif "banned content" in text:
            mb = re.search(r"'([^']+)'", text)
            tags.append(f"BANNED_{(mb.group(1) if mb else '?')[:20]}")
        elif "shot type" in text.lower(): tags.append("NO_SHOT")
        elif "question" in text.lower(): tags.append("QUESTION_COUNT")
        elif "motion" in text.lower(): tags.append("MOTION")
    if not tags:
        if "HTTP" in err: tags.append("HTTP")
        elif "stage" in err.lower(): tags.append("PARSE")
        elif "connection" in err.lower() or "timeout" in err.lower(): tags.append("CONN")
        else: tags.append(err[:40])
    return tags[:5]


def _csv_append(row: dict) -> None:
    path = OUT / "_summary.csv"
    new = not path.exists()
    def cell(v):
        s = str(v)
        return '"' + s.replace('"', '""') + '"' if ("," in s or '"' in s or "\n" in s) else s
    with path.open("a", encoding="utf-8") as f:
        if new:
            f.write(",".join(CSV_COLS) + "\n")
        f.write(",".join(cell(row.get(c, "")) for c in CSV_COLS) + "\n")


def main() -> int:
    settings = Settings()
    settings.local_llm_base_url = BASE_URL
    settings.local_llm_timeout_s = PER_REQUEST_TIMEOUT_S
    arch_fn = ARCHITECTURES[ARCH]

    total = len(COMPETITORS) * len(NICHES) * SCRIPTS_PER_NICHE
    print(f"GEMMA VOLUME BENCH — {len(COMPETITORS)} models × {len(NICHES)} niches × {SCRIPTS_PER_NICHE} scripts = {total} gens")
    print(f"arch={ARCH}  models={[m for _,m in COMPETITORS]}\n", flush=True)
    done = 0
    t_start = time.time()

    for competitor, model_id in COMPETITORS:
        settings.local_llm_model = model_id
        print(f"\n{'#'*70}\n# {competitor}  ({model_id})\n{'#'*70}", flush=True)
        bv2._load_model(BASE_URL, model_id, CTX)
        cdir = OUT / competitor
        cdir.mkdir(parents=True, exist_ok=True)
        for niche in NICHES:
            topics = _load_topics(niche, SCRIPTS_PER_NICHE)
            ndir = cdir / niche
            ndir.mkdir(parents=True, exist_ok=True)
            min_w, max_w, _ = caps_for(niche)
            for idx, topic in enumerate(topics, 1):
                done += 1
                out_path = ndir / f"{idx:02d}.json"
                if out_path.exists():
                    print(f"  [{done}/{total}] skip exists: {competitor}/{niche} #{idx}", flush=True)
                    continue
                print(f"  [{done}/{total}] {competitor} | {niche:14s} #{idx:02d} :: {topic[:60]}", flush=True)
                t0 = time.time()
                row = {"competitor": competitor, "model": model_id, "niche": niche, "idx": idx,
                       "topic": topic[:80], "min_w": min_w, "max_w": max_w}
            try:
                plan, retry_debug = with_retries(
                    arch_fn,
                    settings=settings, niche=niche, topic=topic,
                    max_retries=MAX_RETRIES, timeout_s=PER_REQUEST_TIMEOUT_S,
                )
                elapsed = time.time() - t0
                full = plan.get("full_script", "")
                w = len(full.split())
                s = count_syllables(full)
                in_band = min_w <= w <= max_w
                used = retry_debug.get("succeeded_on", 1)
                row.update(success=True, attempts_used=used,
                           total_time_s=round(elapsed, 1),
                           words=w, syllables=s, in_band=in_band,
                           error_tags="", error="")
                out_path.write_text(
                    json.dumps({"_meta": row, "debug": retry_debug, "plan": plan},
                               ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"      OK {elapsed:.0f}s  attempts={used}  words={w}  in_band={in_band}", flush=True)
            except Exception as e:
                elapsed = time.time() - t0
                err = str(e)[:400]
                tags = _classify_error(err)
                rd = getattr(e, "debug", {})
                used = len(rd.get("attempts", []))
                row.update(success=False, attempts_used=used,
                           total_time_s=round(elapsed, 1),
                           words=0, syllables=0, in_band=False,
                           error_tags=";".join(tags), error=err[:300])
                out_path.write_text(
                    json.dumps({"_meta": row, "debug": rd, "error": err},
                               ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"      FAIL {elapsed:.0f}s  attempts={used}  [{', '.join(tags)}]", flush=True)
            _csv_append(row)

    bv2._unload_all(BASE_URL)
    print(f"\nGEMMA VOLUME DONE in {(time.time()-t_start)/60:.1f} min")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
