"""Competition benchmark — all 8 LM Studio models compete on 17 niches.

Uses MULTI-STAGE generation (plain text → parsed) so reasoning models can
actually finish their answers, and small models don't choke on JSON schema.

Each model writes exactly ONE plan per niche (17 plans / model = 136 total).
Output: scripts_out/competition/<model>/<niche>.json with plan or error +
per-stage debug. Plus competition_results.csv + report.

Run:  python scripts/competition_bench.py
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
from shorts_pipeline.planner.multistage import generate_multistage
from shorts_pipeline.planner.niche_caps import caps_for, count_syllables
import bench_lmstudio_v2 as bv2

MODELS = [
    "gemma-4-e4b",                # Q4_K_M, 4.97GB — baseline
    "google/gemma-4-e4b",         # Q6_K, 6.71GB — fresh download
    "ministral-3-3b-reasoning-2512",
    "llama3.3-8b-instruct-thinking-heretic-uncensored-claude-4.5-opus-high-reasoning-i1",
    "qwen/qwen3.5-9b",
    "deepseek/deepseek-r1-0528-qwen3-8b",
    "mistral-nemo-2407-12b-thinking-claude-gemini-gpt5.2-uncensored-heretic",
    "qwen3.5-9b-deepseek-v4-flash-mtp",
]

NICHES = [
    "business", "cosmic", "crime", "cults", "edutainment", "facts",
    "health", "history", "lost_tech", "military", "mythology",
    "psychology", "science", "sports", "survival", "tech_hackers", "wealth",
]

CTX = 16384  # bigger for reasoning models
PER_REQUEST_TIMEOUT_S = 1500.0

OUT = ROOT / "scripts_out" / "competition"
OUT.mkdir(parents=True, exist_ok=True)


def _slug(model_id: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", model_id.lower()).strip("-")[:80]


def _load_topic(niche: str) -> str:
    """Take the first batch_*.json title for this niche."""
    folder = ROOT / "topics" / "niches" / niche
    for bp in sorted(folder.glob("batch_*.json")):
        try:
            arr = json.loads(bp.read_text(encoding="utf-8"))
            for it in arr:
                if isinstance(it, dict) and (it.get("title") or "").strip():
                    return it["title"].strip()
        except Exception:
            continue
    return "Unknown topic"


def _write_progress(state: dict) -> None:
    (OUT / "_progress.json").write_text(
        json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


CSV_COLS = ["model", "niche", "topic", "success", "time_s", "stage_a_s", "stage_b_s",
            "words", "syllables", "min_w", "max_w", "in_band", "error"]


def _append_csv(row: dict) -> None:
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
    settings.local_llm_timeout_s = PER_REQUEST_TIMEOUT_S
    total = len(MODELS) * len(NICHES)
    print(f"COMPETITION BENCH — {len(MODELS)} models × {len(NICHES)} niches = {total} gens")
    print(f"ctx={CTX}  timeout={PER_REQUEST_TIMEOUT_S}s  output={OUT}\n")

    # Pre-resolve topics
    topics = {n: _load_topic(n) for n in NICHES}

    done = 0
    t_start = time.time()

    for mi, model_id in enumerate(MODELS, 1):
        slug = _slug(model_id)
        mdir = OUT / slug
        mdir.mkdir(parents=True, exist_ok=True)
        settings.local_llm_model = model_id
        print(f"\n{'='*70}\n[{mi}/{len(MODELS)}] {model_id}\n{'='*70}", flush=True)
        bv2._load_model(settings.local_llm_base_url, model_id, CTX)
        model_t0 = time.time()

        for niche in NICHES:
            done += 1
            out_path = mdir / f"{niche}.json"
            if out_path.exists():
                print(f"  [{done}/{total}] skip exists: {niche}", flush=True)
                continue
            topic = topics[niche]
            min_w, max_w, _ = caps_for(niche)
            print(f"  [{done}/{total}] {niche:14s} :: {topic[:60]}", flush=True)
            t0 = time.time()
            row: dict = {"model": model_id, "niche": niche, "topic": topic[:80],
                         "min_w": min_w, "max_w": max_w}
            try:
                plan, debug = generate_multistage(
                    settings, niche=niche, topic=topic, timeout_s=PER_REQUEST_TIMEOUT_S)
                elapsed = time.time() - t0
                full = plan.get("full_script", "")
                w = len(full.split())
                s = count_syllables(full)
                in_band = min_w <= w <= max_w
                row.update(success=True, time_s=round(elapsed, 1),
                           stage_a_s=debug["stages"]["A"]["time_s"],
                           stage_b_s=debug["stages"]["B"]["time_s"],
                           words=w, syllables=s, in_band=in_band, error="")
                out_path.write_text(
                    json.dumps({"_meta": row, "debug": debug, "plan": plan},
                               ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"      ok {elapsed:.0f}s  words={w}  syl={s}  in_band={in_band}", flush=True)
            except Exception as e:
                elapsed = time.time() - t0
                err = str(e)[:400]
                debug = getattr(e, "debug", {})
                row.update(success=False, time_s=round(elapsed, 1),
                           stage_a_s=debug.get("stages", {}).get("A", {}).get("time_s", 0),
                           stage_b_s=debug.get("stages", {}).get("B", {}).get("time_s", 0),
                           words=0, syllables=0, in_band=False, error=err)
                out_path.write_text(
                    json.dumps({"_meta": row, "debug": debug, "error": err},
                               ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"      FAIL {elapsed:.0f}s  {err[:140]}", flush=True)
            _append_csv(row)
            _write_progress({
                "done": done, "total": total,
                "elapsed_min": round((time.time() - t_start) / 60, 1),
                "current_model": model_id, "current_niche": niche,
            })

        print(f"\n  model finished in {(time.time()-model_t0)/60:.1f} min", flush=True)
        bv2._unload_all(settings.local_llm_base_url)

    print(f"\n\nALL DONE in {(time.time()-t_start)/60:.1f} min")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
