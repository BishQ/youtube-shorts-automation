"""Quick benchmark — each remaining model writes 2 plans, see who's closest.

Tests history + science (1 each) for 5 remaining models. Goal: 1 hour total,
quick read on which model has the best chance of passing validation.
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
from shorts_pipeline.planner.schema import NarrationPlan
from shorts_pipeline.planner.structured_output import json_schema_for
from shorts_pipeline.planner.niche_caps import caps_for, count_syllables
import bench_lmstudio_v2 as bv2

MODELS = [
    "llama3.3-8b-instruct-thinking-heretic-uncensored-claude-4.5-opus-high-reasoning-i1",
    "qwen/qwen3.5-9b",
    "deepseek/deepseek-r1-0528-qwen3-8b",
    "mistral-nemo-2407-12b-thinking-claude-gemini-gpt5.2-uncensored-heretic",
    "qwen3.5-9b-deepseek-v4-flash-mtp",
]

# 2 generations per model: one history (longer band) + one science (medium band)
SAMPLES = [
    ("history", "The defenestration of Prague, 1618"),
    ("science", "The Chemist Who Dissolved Himself in Acid on Purpose"),
]

OUT = ROOT / "scripts_out" / "quick_bench"
OUT.mkdir(parents=True, exist_ok=True)


def main() -> int:
    settings = Settings()
    settings.local_llm_timeout_s = 1800.0
    schema = json_schema_for(NarrationPlan, name="NarrationPlan")
    results = []
    t_start = time.time()

    for mi, model_id in enumerate(MODELS, 1):
        print(f"\n{'='*70}\n[{mi}/{len(MODELS)}] {model_id}\n{'='*70}", flush=True)
        bv2._load_model(settings.local_llm_base_url, model_id, 12288)
        settings.local_llm_model = model_id
        for niche, topic in SAMPLES:
            print(f"  -- {niche} :: {topic[:60]}", flush=True)
            t0 = time.time()
            min_w, max_w, _ = caps_for(niche)
            try:
                plan, attempts, errs = bv2.generate_compact(
                    settings, model_id, topic, niche=niche, schema=schema)
                elapsed = time.time() - t0
                full = plan.get("full_script", "")
                w = len(full.split())
                s = count_syllables(full)
                in_band = min_w <= w <= max_w
                print(f"     OK {elapsed:.0f}s  words={w} (band {min_w}-{max_w})  syl={s}  attempts={attempts}  in_band={in_band}", flush=True)
                results.append({"model": model_id, "niche": niche, "topic": topic,
                                "success": True, "time_s": round(elapsed, 1),
                                "words": w, "syllables": s, "min_w": min_w, "max_w": max_w,
                                "in_band": in_band, "attempts": attempts})
            except Exception as e:
                elapsed = time.time() - t0
                err = str(e)[:300]
                # Try to extract word count or syllable count from error
                wc = sc = None
                wm = re.search(r"only (\d+) words", err) or re.search(r"is (\d+) words", err)
                if wm: wc = int(wm.group(1))
                sm = re.search(r"contains (\d+) syllables", err, re.IGNORECASE)
                if sm: sc = int(sm.group(1))
                print(f"     FAIL {elapsed:.0f}s  words={wc}  syl={sc}  err={err[:150]}", flush=True)
                results.append({"model": model_id, "niche": niche, "topic": topic,
                                "success": False, "time_s": round(elapsed, 1),
                                "words": wc, "syllables": sc, "min_w": min_w, "max_w": max_w,
                                "in_band": False, "error": err[:300]})

    bv2._unload_all(settings.local_llm_base_url)

    # Write JSON summary
    (OUT / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")

    # Summary table
    print(f"\n\n=== QUICK BENCH SUMMARY  ({(time.time()-t_start)/60:.1f} min) ===")
    print(f"{'model':50s} {'niche':10s} {'time_s':>7s} {'words':>6s} {'syl':>5s} {'in_band':>8s}  status")
    for r in results:
        wd = r.get('words')
        sd = r.get('syllables')
        ws = str(wd) if wd is not None else "?"
        ss = str(sd) if sd is not None else "?"
        st = "OK" if r['success'] else "FAIL"
        m = r['model']
        if len(m) > 48: m = m[:45] + "..."
        print(f"{m:50s} {r['niche']:10s} {r['time_s']:>7.0f} {ws:>6s} {ss:>5s} {str(r['in_band']):>8s}  {st}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
