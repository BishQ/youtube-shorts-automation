"""LM Studio benchmark v2 — COMPACT prompt + structured output (json_schema).

Differs from v1 (bench_lmstudio_models.py) in two ways:
  1. Uses the short COMPACT_SYSTEM_PROMPT (~600 words) instead of niche prompts.
     Prompt drops from ~16.5K → ~3-4K tokens — fits comfortably in 8GB VRAM
     with KV cache for the full 14-clause plan generation.
  2. Sends `response_format: {type: "json_schema", ...}` so LM Studio enforces
     valid JSON matching the NarrationPlan schema. Eliminates Gemma's
     delimiter errors and similar formatting failures.

Output dir is separate (scripts_out/bench_v2) so v1 results stay intact.

Run:  python scripts/bench_lmstudio_v2.py
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

# Load .env
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
from shorts_pipeline.planner.client import _extract_json  # noqa: E402
from shorts_pipeline.planner.niche_caps import caps_for, count_syllables  # noqa: E402
from shorts_pipeline.planner.niches_compact import make_system_prompt, make_user_prompt  # noqa: E402
from shorts_pipeline.planner.schema import NarrationPlan  # noqa: E402
from shorts_pipeline.planner.structured_output import json_schema_for  # noqa: E402
from shorts_pipeline.planner.word_budget import maybe_clamp_plan_json  # noqa: E402

MODELS = [
    "gemma-4-e4b",
    # ministral-3-3b skipped — tokenizer expands prompt 30%, unfit for this size
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

SCRIPTS_PER_NICHE = 3
CTX_LENGTH = 12288             # plenty for ~4K prompt + 3K completion
PER_REQUEST_TIMEOUT_S = 600.0  # compact prompt should be much faster
MAX_RETRIES = 3
MAX_COMPLETION_TOKENS = 3000

OUT_ROOT = ROOT / "scripts_out" / "bench_v2"
OUT_ROOT.mkdir(parents=True, exist_ok=True)


def _slug(model_id: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", model_id.lower()).strip("-")[:80]


def _load_topics(niche: str, n: int) -> list[dict]:
    folder = ROOT / "topics" / "niches" / niche
    items: list[dict] = []
    for bp in sorted(folder.glob("batch_*.json")):
        try:
            arr = json.loads(bp.read_text(encoding="utf-8"))
        except Exception:
            continue
        for it in arr if isinstance(arr, list) else []:
            if isinstance(it, dict) and (it.get("title") or "").strip():
                items.append({"title": it["title"].strip()})
                if len(items) >= n:
                    return items
    return items


def _api_root(base_url: str) -> str:
    root = base_url.rstrip("/")
    return root[:-3] if root.endswith("/v1") else root


def _unload_all(base_url: str) -> None:
    root = _api_root(base_url)
    try:
        with httpx.Client(timeout=30) as c:
            r = c.get(f"{root}/api/v1/models")
            if r.status_code != 200:
                return
            data = r.json()
            ids: list[str] = []
            for entry in data.get("models") or []:
                for inst in (entry.get("loaded_instances") or []) if isinstance(entry, dict) else []:
                    if isinstance(inst, dict) and inst.get("id"):
                        ids.append(inst["id"])
            for iid in ids:
                try:
                    c.post(f"{root}/api/v1/models/unload", json={"instance_id": iid})
                except Exception:
                    pass
    except Exception:
        pass


def _load_model(base_url: str, model_id: str, ctx: int) -> None:
    _unload_all(base_url)
    root = _api_root(base_url)
    try:
        with httpx.Client(timeout=300) as c:
            r = c.post(
                f"{root}/api/v1/models/load",
                json={"model": model_id, "context_length": ctx},
            )
            print(f"  [load] {model_id} ctx={ctx} -> HTTP {r.status_code}", flush=True)
            if r.status_code >= 400:
                for fb in (8192, 6144, 4096):
                    if fb >= ctx: continue
                    print(f"  [load] retry ctx={fb}", flush=True)
                    r2 = c.post(f"{root}/api/v1/models/load", json={"model": model_id, "context_length": fb})
                    print(f"  [load] HTTP {r2.status_code}", flush=True)
                    if r2.status_code < 400:
                        break
    except Exception as e:
        print(f"  [load] error: {e}", flush=True)


def _classify_error(err: str) -> str:
    e = err.lower()
    if "connection" in e or "refused" in e or "winerror 10061" in e: return "connection"
    if "timeout" in e or "timed out" in e: return "timeout"
    if "n_keep" in e or "n_ctx" in e or "context_length" in e: return "ctx_exceeded"
    if "http 4" in e or "http 5" in e: return "http_error"
    if "validation" in e or "expecting" in e or "field" in e: return "schema_validation"
    return "other"


def _csv_cell(v) -> str:
    s = str(v)
    return '"' + s.replace('"', '""') + '"' if ("," in s or '"' in s or "\n" in s) else s


CSV_COLS = ["model", "niche", "idx", "title", "time_s", "success", "attempts",
            "words", "syllables", "min_w", "max_w", "in_band", "error_type", "error"]


def _append_csv(row: dict) -> None:
    path = OUT_ROOT / "_summary.csv"
    new = not path.exists()
    with path.open("a", encoding="utf-8") as f:
        if new:
            f.write(",".join(CSV_COLS) + "\n")
        f.write(",".join(_csv_cell(row.get(c, "")) for c in CSV_COLS) + "\n")


def _write_progress(state: dict) -> None:
    (OUT_ROOT / "_progress.json").write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def generate_compact(settings: Settings, model_id: str, topic_title: str, *, niche: str, schema: dict) -> tuple[dict, int, list[str]]:
    """Generate one plan using compact niche prompt + structured output. Returns (plan, attempts, attempt_errors)."""
    url = settings.local_llm_base_url.rstrip("/") + "/chat/completions"
    sys_prompt = make_system_prompt(niche)
    user_prompt = make_user_prompt(niche, topic_title, use_figure_name=False)
    validate_ctx = {"allow_figure_name": False, "niche": niche}
    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": user_prompt},
    ]
    attempt_errors: list[str] = []
    for attempt in range(1, MAX_RETRIES + 1):
        payload = {
            "model": model_id,
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": MAX_COMPLETION_TOKENS,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "NarrationPlan", "schema": schema, "strict": True},
            },
        }
        try:
            with httpx.Client(timeout=PER_REQUEST_TIMEOUT_S) as c:
                r = c.post(url, json=payload)
        except httpx.RequestError as e:
            attempt_errors.append(f"a{attempt}: connection: {str(e)[:120]}")
            raise RuntimeError(f"connection error: {e}")
        if r.status_code == 400 and "response_format" in r.text.lower():
            # Some models / LM Studio versions don't support strict json_schema — fall back to json_object
            payload["response_format"] = {"type": "json_object"}
            try:
                with httpx.Client(timeout=PER_REQUEST_TIMEOUT_S) as c:
                    r = c.post(url, json=payload)
            except httpx.RequestError as e:
                attempt_errors.append(f"a{attempt}: connection (fallback): {str(e)[:120]}")
                raise RuntimeError(f"connection error: {e}")
        if r.status_code >= 400:
            err = f"HTTP {r.status_code}: {r.text[:300]}"
            attempt_errors.append(f"a{attempt}: {err[:120]}")
            raise RuntimeError(err)
        raw = (r.json()["choices"][0]["message"]["content"] or "").strip()
        # Strip <think> blocks from reasoning models
        raw = re.sub(r"<think(?:ing)?>.*?</think(?:ing)?>", "", raw, flags=re.DOTALL | re.IGNORECASE).strip()
        try:
            obj = _extract_json(raw)
            maybe_clamp_plan_json(obj)
            plan = NarrationPlan.model_validate(obj, context=validate_ctx)
            return plan.model_dump(mode="json"), attempt, attempt_errors
        except Exception as e:
            attempt_errors.append(f"a{attempt}: {str(e)[:120]}")
            if attempt < MAX_RETRIES:
                messages = [
                    *messages,
                    {"role": "assistant", "content": raw[:1500]},
                    {"role": "user", "content": f"Your last response was invalid: {str(e)[:200]}. Output ONLY one valid JSON object matching the schema."},
                ]
                continue
    raise RuntimeError(f"failed after {MAX_RETRIES} attempts: {attempt_errors[-1] if attempt_errors else 'unknown'}")


def main() -> int:
    settings = Settings()
    settings.local_llm_timeout_s = PER_REQUEST_TIMEOUT_S
    schema = json_schema_for(NarrationPlan, name="NarrationPlan")
    print(f"LM base: {settings.local_llm_base_url}")
    print(f"COMPACT prompt + json_schema  |  ctx={CTX_LENGTH}  |  timeout={PER_REQUEST_TIMEOUT_S}s")
    print(f"Models: {len(MODELS)}  niches: {len(NICHES)}  per-niche: {SCRIPTS_PER_NICHE}")
    total = len(MODELS) * len(NICHES) * SCRIPTS_PER_NICHE
    print(f"Total target: {total} generations")
    print(f"Output: {OUT_ROOT}\n")

    niche_topics = {n: _load_topics(n, SCRIPTS_PER_NICHE) for n in NICHES}

    done = 0
    t_start = time.time()
    for mi, model_id in enumerate(MODELS, 1):
        slug = _slug(model_id)
        mdir = OUT_ROOT / slug
        mdir.mkdir(parents=True, exist_ok=True)
        settings.local_llm_model = model_id
        print(f"\n{'='*70}\n[{mi}/{len(MODELS)}] MODEL: {model_id}\n{'='*70}", flush=True)
        _load_model(settings.local_llm_base_url, model_id, CTX_LENGTH)
        model_t0 = time.time()
        for niche in NICHES:
            topics = niche_topics[niche]
            min_w, max_w, _ = caps_for(niche)
            for ti, topic in enumerate(topics, 1):
                done += 1
                out_path = mdir / f"{niche}__{ti}.json"
                if out_path.exists():
                    print(f"  [{done}/{total}] skip exists: {niche} #{ti}", flush=True)
                    continue
                title = topic["title"][:70]
                print(f"  [{done}/{total}] {niche:14s} #{ti} :: {title}", flush=True)
                t0 = time.time()
                row = {"model": model_id, "niche": niche, "idx": ti, "title": title,
                       "min_w": min_w, "max_w": max_w}
                try:
                    plan, attempts, errs = generate_compact(settings, model_id, topic["title"], niche=niche, schema=schema)
                    elapsed = time.time() - t0
                    full = plan.get("full_script", "")
                    words = len(full.split())
                    syl = count_syllables(full)
                    in_band = min_w <= words <= max_w
                    row.update(time_s=round(elapsed, 1), success=True, attempts=attempts,
                               words=words, syllables=syl, in_band=in_band, error_type="", error="")
                    out_path.write_text(
                        json.dumps({"_meta": row, "attempt_errors": errs, "plan": plan},
                                   ensure_ascii=False, indent=2), encoding="utf-8")
                    print(f"      ok {elapsed:.1f}s  words={words}  syl={syl}  in_band={in_band}  attempts={attempts}", flush=True)
                except Exception as e:
                    elapsed = time.time() - t0
                    err = str(e)[:400]
                    row.update(time_s=round(elapsed, 1), success=False, attempts=MAX_RETRIES,
                               words=0, syllables=0, in_band=False,
                               error_type=_classify_error(err), error=err)
                    out_path.write_text(
                        json.dumps({"_meta": row, "error": err}, ensure_ascii=False, indent=2),
                        encoding="utf-8")
                    print(f"      FAIL {elapsed:.1f}s  type={row['error_type']}  {err[:120]}", flush=True)
                _append_csv(row)
                _write_progress({
                    "done": done, "total": total,
                    "elapsed_min": round((time.time() - t_start) / 60, 1),
                    "current_model": model_id, "current_niche": niche,
                })
        print(f"\n  model finished in {(time.time()-model_t0)/60:.1f} min", flush=True)
        _unload_all(settings.local_llm_base_url)

    print(f"\nALL DONE in {(time.time()-t_start)/60:.1f} min")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
