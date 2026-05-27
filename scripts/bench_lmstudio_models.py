"""Benchmark all LM Studio chat models across every niche.

For each model: load via JIT, generate 3 plans per niche (first 3 topics from
each topics/niches/<niche>/batch_001.json), record time / success / word count
/ syllable count / retry-error info, unload, move to next model.

Output:
  scripts_out/bench/<model_slug>/<niche>__<idx>.json    — full plan or error
  scripts_out/bench/_summary.csv                        — one row per gen
  scripts_out/bench/_progress.json                      — live progress file

Run:
  python scripts/bench_lmstudio_models.py
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

# Load .env so SHORTS_* vars reach Settings.
_env = ROOT / ".env"
if _env.is_file():
    for line in _env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v

from shorts_pipeline.config.settings import Settings  # noqa: E402
from shorts_pipeline.planner.niche_caps import caps_for, count_syllables  # noqa: E402

# Reuse the make_scripts.py plan generation path so behavior matches batch runs.
sys.path.insert(0, str(ROOT))
import make_scripts as ms  # noqa: E402


# Ordered smallest → largest so user sees results faster.
MODELS = [
    "gemma-4-e4b",                                                              # 4.97 GB
    "ministral-3-3b-reasoning-2512",                                             # 6.40 GB BF16
    "llama3.3-8b-instruct-thinking-heretic-uncensored-claude-4.5-opus-high-reasoning-i1",  # 6.14 GB
    "qwen/qwen3.5-9b",                                                           # 6.10 GB
    "deepseek/deepseek-r1-0528-qwen3-8b",                                        # 6.26 GB
    "mistral-nemo-2407-12b-thinking-claude-gemini-gpt5.2-uncensored-heretic",    # 6.33 GB
    "qwen3.5-9b-deepseek-v4-flash-mtp",                                          # 6.47 GB
]

NICHES = [
    "business", "cosmic", "crime", "cults", "edutainment", "facts",
    "health", "history", "lost_tech", "military", "mythology",
    "psychology", "science", "sports", "survival", "tech_hackers", "wealth",
]

SCRIPTS_PER_NICHE = 3
OUT_ROOT = ROOT / "scripts_out" / "bench"
OUT_ROOT.mkdir(parents=True, exist_ok=True)


def _slug(model_id: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", model_id.lower()).strip("-")[:80]


def _load_topics(niche: str, n: int) -> list[dict]:
    folder = ROOT / "topics" / "niches" / niche
    batches = sorted(folder.glob("batch_*.json"))
    if not batches:
        return []
    items: list[dict] = []
    for bp in batches:
        try:
            arr = json.loads(bp.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(arr, list):
            for it in arr:
                if isinstance(it, dict) and (it.get("title") or "").strip():
                    items.append({
                        "unique_id": it.get("unique_id"),
                        "title": it["title"].strip(),
                        "hook": (it.get("hook") or "").strip(),
                        "keywords": it.get("keywords") or [],
                        "subcategory": (it.get("subcategory") or "").strip(),
                    })
                    if len(items) >= n:
                        return items
    return items


def _api_root(base_url: str) -> str:
    root = base_url.rstrip("/")
    if root.endswith("/v1"):
        root = root[:-3]
    return root


def _unload_all(base_url: str) -> None:
    root = _api_root(base_url)
    try:
        with httpx.Client(timeout=30) as c:
            r = c.get(f"{root}/api/v1/models")
            if r.status_code != 200:
                return
            data = r.json()
            if not isinstance(data, dict):
                return
            ids: list[str] = []
            for entry in data.get("models") or []:
                if not isinstance(entry, dict):
                    continue
                for inst in entry.get("loaded_instances") or []:
                    if isinstance(inst, dict) and inst.get("id"):
                        ids.append(inst["id"])
            for iid in ids:
                try:
                    c.post(f"{root}/api/v1/models/unload", json={"instance_id": iid})
                except Exception:
                    pass
    except Exception:
        pass


def _load_with_ctx(base_url: str, model_id: str, ctx: int) -> None:
    """Unload everything, then load model fresh with desired context length."""
    _unload_all(base_url)
    root = _api_root(base_url)
    try:
        with httpx.Client(timeout=300) as c:
            r = c.post(
                f"{root}/api/v1/models/load",
                json={"model": model_id, "context_length": ctx},
            )
            print(f"  [load] {model_id} ctx={ctx} -> HTTP {r.status_code}")
            if r.status_code >= 400:
                # Some smaller models may not support 32K — try a smaller fallback
                for fallback in (24576, 16384, 12288, 8192):
                    if fallback >= ctx:
                        continue
                    print(f"  [load] retry with ctx={fallback}")
                    r2 = c.post(
                        f"{root}/api/v1/models/load",
                        json={"model": model_id, "context_length": fallback},
                    )
                    print(f"  [load] HTTP {r2.status_code}")
                    if r2.status_code < 400:
                        break
    except Exception as e:
        print(f"  [load] error: {e}")


def _write_progress(state: dict) -> None:
    (OUT_ROOT / "_progress.json").write_text(
        json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _quick_stats() -> dict:
    """Aggregate stats from all written JSONs so far. Best-effort."""
    from collections import Counter
    succ = 0; fail = 0; in_band = 0; first_try = 0
    times = []; attempts = []
    err_types = Counter()
    for jf in OUT_ROOT.glob("*/*.json"):
        try:
            d = json.loads(jf.read_text(encoding="utf-8"))
            m = d.get("_meta", {})
        except Exception:
            continue
        if m.get("success"):
            succ += 1
            if m.get("in_band"): in_band += 1
            if m.get("attempts") == 1: first_try += 1
        else:
            fail += 1
            et = m.get("error_type")
            if et: err_types[et] += 1
        t = m.get("time_s")
        if isinstance(t, (int, float)): times.append(t)
        a = m.get("attempts")
        if isinstance(a, int) and a > 0: attempts.append(a)
    return {
        "succ": succ, "fail": fail, "in_band": in_band,
        "first_try": first_try,
        "avg_time_s": round(sum(times)/len(times), 1) if times else 0,
        "avg_attempts": round(sum(attempts)/len(attempts), 2) if attempts else 0,
        "err_types": dict(err_types),
    }


def _append_csv(row: dict) -> None:
    path = OUT_ROOT / "_summary.csv"
    new = not path.exists()
    cols = ["model", "niche", "idx", "title", "time_s", "success", "attempts",
            "words", "syllables", "min_w", "max_w", "in_band",
            "error_type", "error"]
    with path.open("a", encoding="utf-8") as f:
        if new:
            f.write(",".join(cols) + "\n")
        f.write(",".join(_csv_cell(row.get(c, "")) for c in cols) + "\n")


def _csv_cell(v) -> str:
    s = str(v)
    if "," in s or '"' in s or "\n" in s:
        s = '"' + s.replace('"', '""') + '"'
    return s


CTX_LENGTH = 20480  # Just enough for ~16.5K prompt + ~3K completion
PER_REQUEST_TIMEOUT_S = 1800.0  # 30 min per attempt — slow GPU partial-offload


def _classify_error(err: str) -> str:
    e = err.lower()
    if "connection" in e or "refused" in e or "winerror 10061" in e:
        return "connection"
    if "timeout" in e or "timed out" in e:
        return "timeout"
    if "http 4" in e or "http 5" in e:
        return "http_error"
    if "context_length" in e or "n_ctx" in e or "n_keep" in e:
        return "ctx_exceeded"
    if "failed after" in e and "attempts" in e:
        return "schema_validation"
    return "other"


def main() -> int:
    settings = Settings()
    settings.local_llm_timeout_s = PER_REQUEST_TIMEOUT_S  # type: ignore[attr-defined]
    print(f"LM base: {settings.local_llm_base_url}")
    print(f"timeout per request: {PER_REQUEST_TIMEOUT_S}s  ctx target: {CTX_LENGTH}")
    print(f"Models: {len(MODELS)}  niches: {len(NICHES)}  per-niche: {SCRIPTS_PER_NICHE}")
    print(f"Total generations: {len(MODELS) * len(NICHES) * SCRIPTS_PER_NICHE}")
    print(f"Output: {OUT_ROOT}")
    print()

    # Pre-load topics once.
    niche_topics: dict[str, list[dict]] = {}
    for n in NICHES:
        niche_topics[n] = _load_topics(n, SCRIPTS_PER_NICHE)
        if len(niche_topics[n]) < SCRIPTS_PER_NICHE:
            print(f"  [warn] {n}: only {len(niche_topics[n])} topics found")

    total = len(MODELS) * sum(min(len(niche_topics[n]), SCRIPTS_PER_NICHE) for n in NICHES)
    done = 0
    t_start = time.time()

    for mi, model_id in enumerate(MODELS, 1):
        slug = _slug(model_id)
        mdir = OUT_ROOT / slug
        mdir.mkdir(parents=True, exist_ok=True)
        # Point Settings at this model. Settings is a dataclass-like — mutate attr.
        settings.local_llm_model = model_id  # type: ignore[attr-defined]
        os.environ["SHORTS_LOCAL_LLM_MODEL"] = model_id
        print(f"\n{'='*70}\n[{mi}/{len(MODELS)}] MODEL: {model_id}\n{'='*70}")
        _load_with_ctx(settings.local_llm_base_url, model_id, CTX_LENGTH)
        model_t0 = time.time()
        for niche in NICHES:
            sys_prompt, user_fn = ms.load_niche(niche)
            topics = niche_topics[niche]
            min_w, max_w, _ = caps_for(niche)
            for ti, topic in enumerate(topics, 1):
                done += 1
                out_path = mdir / f"{niche}__{ti}.json"
                if out_path.exists():
                    print(f"  [{done}/{total}] skip exists: {niche} #{ti}")
                    continue
                topic_prompt = ms._build_topic_prompt(topic)
                title = topic["title"][:70]
                print(f"  [{done}/{total}] {niche:14s} #{ti} :: {title}", flush=True)
                t0 = time.time()
                row = {
                    "model": model_id, "niche": niche, "idx": ti,
                    "title": title, "min_w": min_w, "max_w": max_w,
                }
                try:
                    plan = ms.generate_plan_for_topic(
                        settings, sys_prompt, user_fn,
                        topic_prompt, niche=niche, backend="local",
                    )
                    elapsed = time.time() - t0
                    full = plan.get("full_script", "")
                    words = len(full.split())
                    syl = count_syllables(full)
                    in_band = min_w <= words <= max_w
                    attempts = getattr(ms, "LAST_ATTEMPTS", 0)
                    attempt_errs = list(getattr(ms, "LAST_ATTEMPT_ERRORS", []))
                    row.update(time_s=round(elapsed, 1), success=True,
                               attempts=attempts,
                               words=words, syllables=syl, in_band=in_band,
                               error_type="", error="")
                    out_path.write_text(
                        json.dumps({"_meta": row, "attempt_errors": attempt_errs, "plan": plan},
                                   ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    print(f"      ok {elapsed:.1f}s  words={words}  syl={syl}  in_band={in_band}  attempts={attempts}", flush=True)
                except Exception as e:
                    elapsed = time.time() - t0
                    err = str(e)[:300]
                    err_type = _classify_error(err)
                    attempts = getattr(ms, "LAST_ATTEMPTS", 0)
                    attempt_errs = list(getattr(ms, "LAST_ATTEMPT_ERRORS", []))
                    row.update(time_s=round(elapsed, 1), success=False,
                               attempts=attempts,
                               words=0, syllables=0, in_band=False,
                               error_type=err_type, error=err)
                    out_path.write_text(
                        json.dumps({"_meta": row, "attempt_errors": attempt_errs, "error": err},
                                   ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    print(f"      FAIL {elapsed:.1f}s  attempts={attempts}  type={err_type}  {err[:120]}", flush=True)
                _append_csv(row)
                # Aggregate stats from CSV so progress file shows live totals.
                stats = _quick_stats()
                _write_progress({
                    "done": done, "total": total,
                    "elapsed_min": round((time.time() - t_start) / 60, 1),
                    "current_model": model_id,
                    "current_niche": niche,
                    "success_total": stats["succ"],
                    "fail_total": stats["fail"],
                    "in_band_total": stats["in_band"],
                    "avg_time_s": stats["avg_time_s"],
                    "avg_attempts": stats["avg_attempts"],
                    "first_try_success": stats["first_try"],
                    "error_types": stats["err_types"],
                })
        model_elapsed = time.time() - model_t0
        print(f"\n  model finished in {model_elapsed/60:.1f} min — unloading")
        _unload_all(settings.local_llm_base_url)

    print(f"\n\nALL DONE in {(time.time()-t_start)/60:.1f} min")
    print(f"Summary CSV: {OUT_ROOT/'_summary.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
