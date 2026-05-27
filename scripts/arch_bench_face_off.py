"""Gemma 4 (local LM Studio) vs DeepSeek (cloud API) — head-to-head face-off.

Both models run the same MAIN architecture across 17 niches. Output goes to
scripts_out/face_off/ so the previous bench dirs stay untouched.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

import httpx

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
ARCH = "MAIN"

# Two competitors: (display name, model_id, base_url, ctx)
COMPETITORS = [
    ("gemma-4-q4",   "gemma-4-e4b",     "http://127.0.0.1:1234/v1",        12288),
    ("deepseek-v4",  "deepseek-chat",   "https://api.deepseek.com/v1",     None),  # cloud
]

PER_REQUEST_TIMEOUT_S = 900.0
MAX_RETRIES = 3

OUT = ROOT / "scripts_out" / "face_off"
OUT.mkdir(parents=True, exist_ok=True)


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60]


def _load_topic(niche: str) -> str:
    folder = ROOT / "topics" / "niches" / niche
    for bp in sorted(folder.glob("batch_*.json")):
        try:
            arr = json.loads(bp.read_text(encoding="utf-8"))
            for it in arr:
                if isinstance(it, dict) and (it.get("title") or "").strip():
                    return it["title"].strip()
        except Exception: continue
    return f"Untitled {niche}"


CSV_COLS = ["competitor", "model", "niche", "topic", "success", "attempts_used",
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
            mm = re.search(r"is (\d+) words.*?(\d+)", text)
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
        elif "timeout" in err.lower() or "connection" in err.lower(): tags.append("CONN")
        else: tags.append(err[:30])
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
    arch_fn = ARCHITECTURES[ARCH]
    topics = {n: _load_topic(n) for n in NICHES}
    total = len(COMPETITORS) * len(NICHES)
    print(f"FACE-OFF — {len(COMPETITORS)} models × {len(NICHES)} niches = {total} gens")
    print(f"arch={ARCH}  max_retries={MAX_RETRIES}\n", flush=True)
    done = 0
    t_start = time.time()

    for competitor_name, model_id, base_url, ctx in COMPETITORS:
        settings = Settings()
        settings.local_llm_base_url = base_url
        settings.local_llm_model = model_id
        settings.local_llm_timeout_s = PER_REQUEST_TIMEOUT_S
        is_local = "127.0.0.1" in base_url or "localhost" in base_url
        print(f"\n{'#'*70}\n# COMPETITOR: {competitor_name}  ({model_id})\n# base_url: {base_url}\n{'#'*70}", flush=True)
        if is_local and ctx:
            bv2._load_model(settings.local_llm_base_url, model_id, ctx)
        cdir = OUT / _slug(competitor_name)
        cdir.mkdir(parents=True, exist_ok=True)
        for niche in NICHES:
            done += 1
            out_path = cdir / f"{niche}.json"
            if out_path.exists():
                print(f"  [{done}/{total}] skip exists: {competitor_name}/{niche}", flush=True)
                continue
            topic = topics[niche]
            min_w, max_w, _ = caps_for(niche)
            print(f"  [{done}/{total}] {competitor_name:14s} | {niche:14s} :: {topic[:60]}", flush=True)
            t0 = time.time()
            row = {"competitor": competitor_name, "model": model_id, "niche": niche,
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
        if is_local:
            bv2._unload_all(settings.local_llm_base_url)

    print(f"\nFACE-OFF DONE in {(time.time()-t_start)/60:.1f} min")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
