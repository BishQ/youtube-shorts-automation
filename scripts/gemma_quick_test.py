"""One-off: MAIN arch + Gemma on 3 random niches."""

from __future__ import annotations

import json
import os
import random
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
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val

import bench_lmstudio_v2 as bv2  # noqa: E402
from shorts_pipeline.config.settings import Settings  # noqa: E402
from shorts_pipeline.planner.cinematic import ARCHITECTURES, with_retries  # noqa: E402
from shorts_pipeline.planner.niche_caps import caps_for, count_syllables  # noqa: E402

ALL_NICHES = [
    "business", "cosmic", "crime", "cults", "edutainment", "facts",
    "health", "history", "lost_tech", "military", "mythology",
    "psychology", "science", "sports", "survival", "tech_hackers", "wealth",
]

MODEL = "gemma-4-e4b"
CTX = 12288
TIMEOUT_S = 900.0
MAX_RETRIES = 3
MAIN = ARCHITECTURES["MAIN"]


def _load_topic(niche: str) -> str:
    folder = ROOT / "topics" / "niches" / niche
    for bp in sorted(folder.glob("batch_*.json")):
        try:
            arr = json.loads(bp.read_text(encoding="utf-8"))
        except Exception:
            continue
        for it in arr:
            if isinstance(it, dict) and (it.get("title") or "").strip():
                return it["title"].strip()
    return f"Untitled {niche} topic"


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--niches",
        default="",
        help="Comma-separated niches (default: 3 random)",
    )
    args = ap.parse_args()
    if args.niches.strip():
        picked = [n.strip() for n in args.niches.split(",") if n.strip()]
    else:
        picked = random.sample(ALL_NICHES, 3)
    print("PICKED_NICHES:", ",".join(picked), flush=True)

    settings = Settings()
    settings.local_llm_model = MODEL
    settings.local_llm_timeout_s = TIMEOUT_S
    print(f"LM: {settings.local_llm_base_url}  model={MODEL}", flush=True)
    print("Loading model...", flush=True)
    bv2._load_model(settings.local_llm_base_url, MODEL, CTX)

    results: list[dict] = []
    for niche in picked:
        topic = _load_topic(niche)
        min_w, max_w, _ = caps_for(niche)
        print(f"\n=== {niche} :: {topic[:70]} (words {min_w}-{max_w}) ===", flush=True)
        t0 = time.time()
        row: dict = {"niche": niche, "topic": topic, "pass": False}
        try:
            plan, dbg = with_retries(
                MAIN,
                settings=settings,
                niche=niche,
                topic=topic,
                max_retries=MAX_RETRIES,
                timeout_s=TIMEOUT_S,
            )
            full = plan.get("full_script", "")
            w = len(full.split())
            in_band = min_w <= w <= max_w
            stages = dbg["attempts"][-1].get("stages", {}).get("stages", {})
            row.update(
                pass_ok=True,
                attempts=dbg.get("succeeded_on", 1),
                seconds=round(time.time() - t0, 1),
                words=w,
                syllables=count_syllables(full),
                in_band=in_band,
                stages=stages,
                clauses=len(plan.get("clauses", [])),
            )
            print(
                f"PASS attempt={row['attempts']} {row['seconds']}s "
                f"words={w} in_band={in_band}",
                flush=True,
            )
            for k, v in stages.items():
                print(f"  stage {k}: {v}", flush=True)
        except Exception as exc:
            row["seconds"] = round(time.time() - t0, 1)
            row["error"] = str(exc)[:400]
            rd = getattr(exc, "debug", {})
            row["attempts"] = len(rd.get("attempts", []))
            print(f"FAIL {row['seconds']}s attempts={row['attempts']}", flush=True)
            print(f"  err: {row['error'][:200]}", flush=True)
        results.append(row)

    bv2._unload_all(settings.local_llm_base_url)
    out = ROOT / "scripts_out" / "gemma_quick_test.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({"model": MODEL, "niches": picked, "results": results}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print("\nSUMMARY", flush=True)
    for r in results:
        status = "PASS" if r.get("pass_ok") else "FAIL"
        if r.get("pass_ok"):
            extra = f"words={r.get('words')} in_band={r.get('in_band')}"
        else:
            extra = (r.get("error") or "")[:80]
        print(f"  {r['niche']:14s} {status}  {extra}", flush=True)
    print(f"saved: {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
