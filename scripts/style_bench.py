"""Prompt-style competition bench.

Tests 5 different prompt styles against the same model across all 17 niches.
Goal: figure out which prompt style produces the highest pass rate, then we
can iterate on the worst-performing field.

Run:  python scripts/style_bench.py [--model MODEL_ID] [--niches N1,N2,...]
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
from shorts_pipeline.planner.multistage import _post, parse_stage_a, parse_stage_b
from shorts_pipeline.planner.multistage import stage_b_system, stage_b_user
from shorts_pipeline.planner.niche_caps import caps_for, count_syllables
from shorts_pipeline.planner.prompt_styles import STYLE_NAMES, build_prompts
from shorts_pipeline.planner.schema import NarrationPlan
import bench_lmstudio_v2 as bv2

# Default models: smallest first so we see results fast; ministral skipped
# (its tokenizer expands prompts by 30%, unfit for this size). Largest models
# last so the slow ones don't block early data.
DEFAULT_MODELS = [
    "gemma-4-e4b",                                                                              # Q4 ~5 GB
    "google/gemma-4-e4b",                                                                       # Q6 ~6.7 GB
    "llama3.3-8b-instruct-thinking-heretic-uncensored-claude-4.5-opus-high-reasoning-i1",       # 6.14 GB
    "qwen/qwen3.5-9b",                                                                          # 6.10 GB
    "deepseek/deepseek-r1-0528-qwen3-8b",                                                       # 6.26 GB
    "mistral-nemo-2407-12b-thinking-claude-gemini-gpt5.2-uncensored-heretic",                   # 6.33 GB
    "qwen3.5-9b-deepseek-v4-flash-mtp",                                                         # 6.47 GB
]
DEFAULT_MODEL = DEFAULT_MODELS[0]  # back-compat
DEFAULT_NICHES = [
    "history", "science", "crime", "mythology", "cosmic",
    "psychology", "military", "wealth", "business", "facts",
    "edutainment", "health", "lost_tech", "sports", "survival",
    "tech_hackers", "cults",
]

CTX = 12288
PER_REQUEST_TIMEOUT_S = 600.0

OUT_ROOT = ROOT / "scripts_out" / "style_bench"
OUT_ROOT.mkdir(parents=True, exist_ok=True)


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


def _classify_validation_error(err_msg: str) -> list[str]:
    """Pull the most useful 1-3 short tags from a pydantic / runtime error."""
    tags: list[str] = []
    for m in re.finditer(r"Value error, ([^\n]+)", err_msg):
        text = m.group(1)
        if "is only" in text and "minimum is" in text:
            mm = re.search(r"only (\d+) words.*minimum is (\d+)", text)
            tags.append(f"SHORT_{mm.group(1)}<{mm.group(2)}" if mm else "SHORT")
        elif "word limit" in text.lower():
            mm = re.search(r"is (\d+) words.*limit of (\d+)", text)
            tags.append(f"LONG_{mm.group(1)}>{mm.group(2)}" if mm else "LONG")
        elif "syllable" in text.lower():
            mm = re.search(r"contains (\d+) syllables.*?(\d+)", text)
            tags.append(f"SYL_{mm.group(1)}>{mm.group(2)}" if mm else "SYL")
        elif "banned content" in text:
            mb = re.search(r"'([^']+)'", text)
            tags.append(f"BANNED_{(mb.group(1) if mb else '?')[:20]}")
        elif "lighting" in text.lower():
            tags.append("NO_LIGHTING")
        elif "shot type" in text.lower():
            tags.append("NO_SHOT")
        elif "question" in text.lower():
            tags.append("QUESTION_COUNT")
        elif "motion" in text.lower():
            tags.append("MOTION")
        elif "date" in text.lower():
            tags.append("DATE")
        elif "figure" in text.lower():
            tags.append("FIGURE_NAME")
        elif "clause" in text.lower():
            tags.append("CLAUSE_COUNT")
        else:
            tags.append(text[:40].replace("\n", " "))
    if not tags:
        if "HTTP" in err_msg: tags.append("HTTP_ERROR")
        elif "stage A" in err_msg: tags.append("STAGE_A_PARSE")
        elif "stage B" in err_msg: tags.append("STAGE_B_PARSE")
        elif "connection" in err_msg.lower(): tags.append("CONNECTION")
        elif "timeout" in err_msg.lower(): tags.append("TIMEOUT")
        else: tags.append(err_msg[:30])
    return tags[:5]


def _csv_append(row: dict) -> None:
    cols = ["model", "style", "niche", "topic", "success", "time_s",
            "words", "syllables", "min_w", "max_w", "in_band", "error_tags", "error"]
    path = OUT_ROOT / "_summary.csv"
    new = not path.exists()
    def cell(v):
        s = str(v)
        return '"' + s.replace('"', '""') + '"' if ("," in s or '"' in s or "\n" in s) else s
    with path.open("a", encoding="utf-8") as f:
        if new:
            f.write(",".join(cols) + "\n")
        f.write(",".join(cell(row.get(c, "")) for c in cols) + "\n")


def _stage_payload(model: str, sys_p: str, user_p: str, max_tokens: int) -> dict:
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": sys_p},
            {"role": "user", "content": user_p},
        ],
        "temperature": 0.2,
        "max_tokens": max_tokens,
    }


def generate_with_style(settings: Settings, *, niche: str, topic: str, style: str) -> tuple[dict, dict]:
    """Run multistage with the given prompt style on Stage A. Stage B unchanged."""
    debug: dict = {"stages": {}, "style": style}
    sysA, userA = build_prompts(style, niche, topic)
    payloadA = _stage_payload(settings.local_llm_model, sysA, userA, max_tokens=4000)
    tA = time.time()
    rawA = _post(settings, payloadA, timeout=PER_REQUEST_TIMEOUT_S)
    debug["stages"]["A"] = {"time_s": round(time.time() - tA, 1), "chars": len(rawA)}
    debug["raw_A"] = rawA[:5000]
    partial = parse_stage_a(rawA)
    if len([c for c in partial["clauses"] if c["text"]]) < 14:
        n = len([c for c in partial['clauses'] if c['text']])
        e = RuntimeError(f"stage A parse: only {n}/14 clauses")
        e.debug = debug
        raise e

    sysB = stage_b_system(niche)
    userB = stage_b_user(partial["clauses"])
    payloadB = _stage_payload(settings.local_llm_model, sysB, userB, max_tokens=4000)
    tB = time.time()
    rawB = _post(settings, payloadB, timeout=PER_REQUEST_TIMEOUT_S)
    debug["stages"]["B"] = {"time_s": round(time.time() - tB, 1), "chars": len(rawB)}
    debug["raw_B"] = rawB[:5000]
    visuals = parse_stage_b(rawB)
    parsed = sum(1 for v in visuals if v["image_prompt"])
    debug["stages"]["B"]["parsed"] = parsed
    if parsed < 14:
        e = RuntimeError(f"stage B parse: only {parsed}/14 visuals")
        e.debug = debug
        raise e

    merged = []
    for c, v in zip(partial["clauses"], visuals):
        merged.append({
            "text": c["text"],
            "image_prompt": v["image_prompt"],
            "motion_prompt": v["motion_prompt"],
            "figure_present": v["figure_present"],
            "beat": v["beat"],
        })
    plan = {
        "historical_figure": partial["historical_figure"] or topic,
        "cold_open_object": partial["cold_open_object"] or "an unnamed object",
        "decision_lever": partial["decision_lever"],
        "clauses": merged,
        "full_script": partial["full_script"],
        "lut_choice": partial["lut_choice"],
        "end_plate_question": partial["end_plate_question"],
    }
    validated = NarrationPlan.model_validate(
        plan, context={"allow_figure_name": False, "niche": niche})
    return validated.model_dump(mode="json"), debug


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default=",".join(DEFAULT_MODELS),
                    help="comma-separated model IDs to test (default: all 7 viable models)")
    ap.add_argument("--niches", default=",".join(DEFAULT_NICHES))
    ap.add_argument("--styles", default=",".join(STYLE_NAMES))
    args = ap.parse_args()

    settings = Settings()
    settings.local_llm_timeout_s = PER_REQUEST_TIMEOUT_S

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    niches = [n.strip() for n in args.niches.split(",") if n.strip()]
    styles = [s.strip() for s in args.styles.split(",") if s.strip()]
    total = len(models) * len(styles) * len(niches)
    print(f"STYLE BENCH — {len(models)} models × {len(styles)} styles × {len(niches)} niches = {total} gens")
    print(f"models: {models}")
    print(f"styles: {styles}")
    print(f"niches: {niches}")
    print(f"output: {OUT_ROOT}\n", flush=True)

    topics = {n: _load_topic(n) for n in niches}

    done = 0
    t_start = time.time()
    for mi, model_id in enumerate(models, 1):
        settings.local_llm_model = model_id
        print(f"\n{'#'*70}\n# [{mi}/{len(models)}] MODEL: {model_id}\n{'#'*70}", flush=True)
        bv2._load_model(settings.local_llm_base_url, model_id, CTX)
        model_t0 = time.time()
        for style in styles:
            print(f"\n{'='*70}\nSTYLE: {style}\n{'='*70}", flush=True)
            sdir = OUT_ROOT / _slug(model_id) / style
            sdir.mkdir(parents=True, exist_ok=True)
            for niche in niches:
                done += 1
                out_path = sdir / f"{niche}.json"
                if out_path.exists():
                    print(f"  [{done}/{total}] skip exists: {style}/{niche}", flush=True)
                    continue
                topic = topics[niche]
                min_w, max_w, _ = caps_for(niche)
                print(f"  [{done}/{total}] {style:9s} | {niche:14s} :: {topic[:60]}", flush=True)
                t0 = time.time()
                row = {"model": model_id, "style": style, "niche": niche,
                       "topic": topic[:80], "min_w": min_w, "max_w": max_w}
                try:
                    plan, debug = generate_with_style(settings, niche=niche, topic=topic, style=style)
                    elapsed = time.time() - t0
                    full = plan.get("full_script", "")
                    w = len(full.split())
                    s = count_syllables(full)
                    in_band = min_w <= w <= max_w
                    row.update(success=True, time_s=round(elapsed, 1),
                               words=w, syllables=s, in_band=in_band,
                               error_tags="", error="")
                    out_path.write_text(
                        json.dumps({"_meta": row, "debug": debug, "plan": plan},
                                   ensure_ascii=False, indent=2), encoding="utf-8")
                    print(f"      OK {elapsed:.0f}s  words={w}  syl={s}  in_band={in_band}", flush=True)
                except Exception as e:
                    elapsed = time.time() - t0
                    err = str(e)[:400]
                    tags = _classify_validation_error(err)
                    debug = getattr(e, "debug", {})
                    row.update(success=False, time_s=round(elapsed, 1),
                               words=0, syllables=0, in_band=False,
                               error_tags=";".join(tags), error=err[:300])
                    out_path.write_text(
                        json.dumps({"_meta": row, "debug": debug, "error": err},
                                   ensure_ascii=False, indent=2), encoding="utf-8")
                    print(f"      FAIL {elapsed:.0f}s  [{', '.join(tags)}]", flush=True)
                _csv_append(row)
        print(f"\n  model {model_id} finished in {(time.time()-model_t0)/60:.1f} min", flush=True)
        bv2._unload_all(settings.local_llm_base_url)

    print(f"\nALL DONE in {(time.time()-t_start)/60:.1f} min")
    print(f"\nRun:  python scripts/style_report.py  for breakdown")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
