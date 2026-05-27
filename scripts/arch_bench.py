"""Cinematic architecture competition.

Five different multi-stage pipelines compete on the same (niche, topic). Each
runs with a 3-retry feedback loop. Goal: see which architecture is most
robust on low-VRAM local hardware.

Run: python scripts/arch_bench.py [--archs A,B,C,D,E] [--niches ...] [--models ...]
"""

from __future__ import annotations

import argparse
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


DEFAULT_MODELS = [
    # Skipped: gemma-4-e4b Q4, google/gemma-4-e4b Q6, qwen/qwen3.5-9b
    "qwen3.5-9b-deepseek-v4-flash-mtp",
    "deepseek/deepseek-r1-0528-qwen3-8b",
    "llama3.3-8b-instruct-thinking-heretic-uncensored-claude-4.5-opus-high-reasoning-i1",
    "mistral-nemo-2407-12b-thinking-claude-gemini-gpt5.2-uncensored-heretic",
]

DEFAULT_NICHES = [
    "business", "cosmic", "crime", "cults", "edutainment", "facts",
    "health", "history", "lost_tech", "military", "mythology",
    "psychology", "science", "sports", "survival", "tech_hackers", "wealth",
]
DEFAULT_ARCHS = list(ARCHITECTURES.keys())

CTX = 12288
PER_REQUEST_TIMEOUT_S = 600.0
MAX_RETRIES = 3

OUT = ROOT / "scripts_out" / "arch_bench"
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
        except Exception:
            continue
    return f"Untitled {niche} topic"


CSV_COLS = ["model", "arch", "niche", "topic", "success", "attempts_used",
            "total_time_s", "words", "syllables", "min_w", "max_w",
            "in_band", "error_tags", "error"]


def _classify_error(err: str) -> list[str]:
    tags = []
    for m in re.finditer(r"Value error, ([^|\n]+)", err):
        text = m.group(1)
        if "is only" in text and "minimum is" in text:
            mm = re.search(r"only (\d+) words.*minimum is (\d+)", text)
            tags.append(f"SHORT_{mm.group(1)}<{mm.group(2)}" if mm else "SHORT")
        elif "word limit" in text.lower():
            mm = re.search(r"is (\d+) words.*?(\d+)", text)
            tags.append(f"LONG_{mm.group(1)}>{mm.group(2)}" if mm else "LONG")
        elif "syllable" in text.lower():
            mm = re.search(r"(\d+) syllables.*?(\d+)", text)
            tags.append(f"SYL_{mm.group(1)}>{mm.group(2)}" if mm else "SYL")
        elif "banned content" in text:
            mb = re.search(r"'([^']+)'", text)
            tags.append(f"BANNED_{(mb.group(1) if mb else '?')[:20]}")
        elif "lighting" in text.lower(): tags.append("NO_LIGHTING")
        elif "shot type" in text.lower(): tags.append("NO_SHOT")
        elif "question" in text.lower(): tags.append("QUESTION_COUNT")
        elif "motion" in text.lower(): tags.append("MOTION")
        elif "date" in text.lower(): tags.append("DATE")
    if not tags:
        for kw in ("stage A", "stage B", "stage1", "stage2", "stage3", "skeleton", "shell"):
            if kw in err: tags.append(f"PARSE_{kw}"); break
        if "HTTP" in err: tags.append("HTTP")
        elif "connection" in err.lower(): tags.append("CONN")
        elif "timeout" in err.lower(): tags.append("TIMEOUT")
        elif not tags: tags.append(err[:30])
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
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default=",".join(DEFAULT_MODELS))
    ap.add_argument("--niches", default=",".join(DEFAULT_NICHES))
    ap.add_argument("--archs", default=",".join(DEFAULT_ARCHS))
    args = ap.parse_args()

    settings = Settings()
    settings.local_llm_timeout_s = PER_REQUEST_TIMEOUT_S

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    niches = [n.strip() for n in args.niches.split(",") if n.strip()]
    archs = [a.strip() for a in args.archs.split(",") if a.strip()]
    total = len(models) * len(archs) * len(niches)
    print(f"ARCH BENCH — {len(models)} models × {len(archs)} archs × {len(niches)} niches = {total} gens")
    print(f"max_retries per gen: {MAX_RETRIES}")
    print(f"models: {models}")
    print(f"archs:  {archs}")
    print(f"niches: {niches}\n", flush=True)

    topics = {n: _load_topic(n) for n in niches}

    done = 0
    t_start = time.time()
    for mi, model_id in enumerate(models, 1):
        settings.local_llm_model = model_id
        print(f"\n{'#'*70}\n# [{mi}/{len(models)}] MODEL: {model_id}\n{'#'*70}", flush=True)
        bv2._load_model(settings.local_llm_base_url, model_id, CTX)
        for arch in archs:
            arch_fn = ARCHITECTURES[arch]
            for niche in niches:
                done += 1
                topic = topics[niche]
                min_w, max_w, _ = caps_for(niche)
                out_dir = OUT / _slug(model_id) / arch
                out_dir.mkdir(parents=True, exist_ok=True)
                out_path = out_dir / f"{niche}.json"
                if out_path.exists():
                    print(f"  [{done}/{total}] skip exists: {arch}/{niche}", flush=True)
                    continue
                print(f"  [{done}/{total}] {arch:14s} | {niche:14s} :: {topic[:60]}", flush=True)
                t0 = time.time()
                row = {"model": model_id, "arch": arch, "niche": niche,
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
        bv2._unload_all(settings.local_llm_base_url)

    print(f"\nALL DONE in {(time.time()-t_start)/60:.1f} min")
    print(f"Run:  python scripts/arch_report.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
