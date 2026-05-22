"""Standalone batch script writer — niche-prompt JSON plans, no pipeline touch.

Reads topics from one of several formats and generates a NarrationPlan JSON for
each topic using the niche-specific prompt in `src/shorts_pipeline/planner/niches/`.
Does not create jobs, does not call the image or audio pipeline — just produces
plan JSONs.

Accepted topic sources:
  - niche_progress folder (batch_*.json from topic creator) — RICH: passes title,
    hook, keywords, subcategory into the planner prompt. Tracks completed
    unique_ids in scripts_out/_used_topics.json so reruns never repeat work.
  - folder with 1.txt … N.txt (one topic per file)
  - folder with topics.txt (legacy famous_people_1000 batches)
  - single .txt file (one topic per line)

Usage:
    python make_scripts.py <niche> <batch_path> [--out <dir>] [--limit N] [--start N]

Examples:
    # Recommended — niche_progress (rich topics, dedup via unique_id):
    python make_scripts.py crime       "C:/Users/35383/Documents/topic creator/niche_progress/crime"
    python make_scripts.py mythology   "C:/Users/35383/Documents/topic creator/niche_progress/mythology"
    python make_scripts.py cosmic      "C:/Users/35383/Documents/topic creator/niche_progress/cosmic"

    # Documentary (biographies) — famous_people_1000 catalog:
    python make_scripts.py documentary "C:/Users/35383/Documents/topic creator/output/famous_people_1000/batch_001"

    # Ad-hoc list:
    python make_scripts.py crime topics.txt --limit 10

    # Local Qwen 3.6 27B via Ollama (no cloud API):
    ollama pull qwen3.6:27b
    python make_scripts.py history test_topics.txt --backend local --limit 3
    # Or: set SHORTS_LOCAL_LLM_ONLY=1 in .env
"""

from __future__ import annotations

import argparse
import importlib
import json
import re
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from shorts_pipeline.config.settings import Settings  # noqa: E402
from shorts_pipeline.planner.client import _extract_json  # noqa: E402
from shorts_pipeline.planner.schema import NarrationPlan  # noqa: E402
from shorts_pipeline.planner.word_budget import maybe_clamp_plan_json  # noqa: E402


_NUM_RE = re.compile(r"(\d+)")
_SLUG_RE = re.compile(r"[^a-z0-9]+")
# topic creator topics.txt lines: "1. John Napier" or "1) John Napier"
_LINE_NUM_PREFIX = re.compile(r"^\s*\d+[\.\)]\s*")


def _numeric_key(p: Path) -> tuple[int, str]:
    m = _NUM_RE.search(p.stem)
    return (int(m.group(1)) if m else 10**9, p.name.lower())


def _slugify(text: str, max_len: int = 60) -> str:
    s = _SLUG_RE.sub("-", text.lower()).strip("-")
    return s[:max_len] or "topic"


def _clean_topic_line(line: str) -> str:
    """Strip topic-creator numbering ('1. Name') and take first line only."""
    line = line.strip()
    if not line:
        return ""
    line = _LINE_NUM_PREFIX.sub("", line).strip()
    return line.splitlines()[0].strip()


def _topics_from_lines(lines: list[str]) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for i, raw in enumerate(lines, 1):
        topic = _clean_topic_line(raw)
        if not topic:
            continue
        m = _NUM_RE.match(raw.strip())
        idx = int(m.group(1)) if m else i
        out.append((idx, topic))
    return out


def _topics_from_niche_progress(batch_dir: Path) -> list[tuple[int, dict]]:
    """Read niche_progress/<niche>/batch_*.json and yield rich topic dicts.

    Each topic carries title + hook + keywords + unique_id so the planner can
    use the hook as opening angle instead of inferring one from the bare title.
    Index runs sequentially across all batches (1, 2, 3, … 5000) so filenames
    are stable across multiple runs even as new batches are added.
    """
    batches = sorted(batch_dir.glob("batch_*.json"))
    out: list[tuple[int, dict]] = []
    counter = 0
    for bp in batches:
        try:
            items = json.loads(bp.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[warn] skipping {bp.name}: {e}", file=sys.stderr)
            continue
        if not isinstance(items, list):
            continue
        for it in items:
            title = (it.get("title") or "").strip()
            if not title:
                continue
            counter += 1
            out.append((counter, {
                "unique_id": it.get("unique_id"),
                "title": title,
                "hook": (it.get("hook") or "").strip(),
                "keywords": it.get("keywords") or [],
                "subcategory": (it.get("subcategory") or "").strip(),
            }))
    return out


def load_topics(batch_path: Path) -> list[tuple[int, str | dict]]:
    """Return [(index, topic_text_or_dict)] sorted by numeric prefix.

    Accepts:
      - folder with batch_*.json (topic creator niche_progress; rich dicts with hook/keywords)
      - folder with 1.txt, 2.txt, … (one topic per file)
      - folder with topics.txt (topic creator famous_people_1000 batches)
      - single .txt file (one topic per line, optional 'N. ' prefix)
    """
    if batch_path.is_dir():
        if list(batch_path.glob("batch_*.json")):
            return _topics_from_niche_progress(batch_path)
        numbered = [p for p in batch_path.glob("*.txt") if p.stem.isdigit()]
        if numbered:
            files = sorted(numbered, key=_numeric_key)
            out: list[tuple[int, str]] = []
            for i, f in enumerate(files, 1):
                txt = f.read_text(encoding="utf-8").strip()
                if not txt:
                    continue
                m = _NUM_RE.search(f.stem)
                idx = int(m.group(1)) if m else i
                topic = _clean_topic_line(txt)
                if topic:
                    out.append((idx, topic))
            return out
        topics_file = batch_path / "topics.txt"
        if topics_file.is_file():
            lines = topics_file.read_text(encoding="utf-8").splitlines()
            return _topics_from_lines(lines)
        raise SystemExit(
            f"no topics in {batch_path}: expected 1.txt…N.txt or topics.txt"
        )
    if batch_path.is_file():
        lines = batch_path.read_text(encoding="utf-8").splitlines()
        return _topics_from_lines(lines)
    raise SystemExit(f"batch_path not found: {batch_path}")


def load_niche(niche: str):
    """Import niches.<niche> and return (system_prompt, user_prompt_fn)."""
    mod = importlib.import_module(f"shorts_pipeline.planner.niches.{niche}")
    sys_prompt = getattr(mod, "SYSTEM_PROMPT")
    user_fn = getattr(mod, "user_prompt")
    return sys_prompt, user_fn


# ────────────────────────────────────────────────────────────────────────────
# Schema patch appended to every initial user prompt
# Overrides stale/wrong enum values that some niche JSON_SHAPE_BLOCKs have.
# ────────────────────────────────────────────────────────────────────────────

def _niche_budget_block(niche: str | None) -> str:
    """Niche-specific TOTAL word + syllable budgets only.

    No per-clause budget — clauses are free to be any length (5 words, 25 words,
    whatever) as long as the TOTAL stays in the band. The image-alignment pipeline
    syncs visuals to whisper-aligned word timestamps, so uneven clause durations
    are fine. Forcing per-clause budgets earlier pushed DeepSeek into a verbose
    floor it couldn't escape; total-only caps let it distribute words naturally.
    """
    from shorts_pipeline.planner.niche_caps import caps_for
    min_w, max_w, max_syl = caps_for(niche)
    label = niche or "default"
    return (
        "\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ HARD TTS BUDGET — {label.upper()} NICHE ⚡\n"
        "(Calibrated from 47 real Kokoro renders. Validator rejects anything over.)\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"\n"
        f"📏 full_script TOTAL: {min_w}–{max_w} words AND ≤{max_syl} syllables.\n"
        f"   These are TOTALS across all 14 clauses combined.\n"
        f"   Distribute words however you like — one clause can be 5 words, another 22.\n"
        f"   Only the TOTAL matters. There is NO per-clause word budget.\n"
        f"\n"
        "🛑 BANNED — words that burn syllable budget for free (replace with shorter):\n"
        "   characteristics(5)→traits(1) | demonstration(4)→proof(1) | extraordinarily(6)→wildly(2)\n"
        "   investigation(5)→probe(1)   | logarithmic(4)→log(1)     | illustration(4)→sketch(1)\n"
        "   revolutionary(5)→radical(3) | mathematics(4)→math(1)    | philosophical(5)→deep(1)\n"
        "   interpretation(5)→read(1)   | unprecedented(5)→new(1)   | infrastructure(4)→system(2)\n"
        "\n"
        "✅ Self-check BEFORE writing the JSON: count the words in your full_script. "
        f"If above {max_w} OR below {min_w}, rewrite. If words pass but syllables exceed "
        f"{max_syl}, swap the heaviest 3–5 words for shorter equivalents.\n"
    )


_SCHEMA_PATCH = """\

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STRICT SCHEMA — FINAL AUTHORITY (overrides anything above)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Your JSON MUST include "historical_figure" as a required top-level field
(the subject's full name, e.g. "Jamukha", "Isaac Newton").

EXACT valid enum values — use NOTHING else:
• lever_type    : "law" | "geography" | "politics"
• color_grade   : "epic_warm" | "tragic_cold" | "ancient_sepia" | "dark_thriller" | "golden_hour" | null
• audio_event   : "none" | "low_rumble" | "impact" | "paper_flutter" | "crowd_cheer"
                  | "sword_clash" | "horse_gallop" | "fire_crackle" | "thunder_crack" | "crowd_murmur"
• lut_choice    : "epic_warm" | "tragic_cold" | "ancient_sepia" | "dark_thriller" | "golden_hour"

Every image_prompt MUST contain:
  1. A shot type word from: close-up, wide shot, medium shot, low-angle, high-angle,
     dutch angle, establishing, extreme close-up, extreme wide shot, portrait shot,
     tracking shot, aerial shot, top-down, hero shot, hero portrait, over-the-shoulder
  2. A lighting word (light, shadow, candle, dawn, dusk, glow, torchlight, etc.)

Every motion_prompt MUST:
  1. Describe MOTION only (camera move + subject action + atmosphere) — NOT scene content
  2. Include a camera-move verb: push-in, pull-back, dolly, pan, tilt, orbit, tracks, zoom,
     static shot, or handheld
  3. Be unique per clause (never duplicate the same motion_prompt twice)
  4. Match beat.camera: ken_burns→push-in, pan→pan, zoom_out→pull-back, hold→static drift,
     parallax→orbit
"""

_MAX_RETRIES = 10


# ────────────────────────────────────────────────────────────────────────────
# Local Qwen (Ollama / OpenAI-compatible) — messages list with correction retries
# ────────────────────────────────────────────────────────────────────────────

_THINK_BLOCK_RE = re.compile(
    r"(?:<think(?:ing)?>.*?</think(?:ing)?>|<think>.*?</think>)",
    re.DOTALL | re.IGNORECASE,
)


def _strip_think_blocks(raw: str) -> str:
    """Remove Qwen thinking blocks before JSON extraction."""
    return _THINK_BLOCK_RE.sub("", raw).strip()


def local_qwen_generate_with_retry(settings: Settings, system_prompt: str,
                                   first_user_msg: str, *, niche: str | None = None) -> dict:
    if not settings.local_llm_model:
        raise RuntimeError("SHORTS_LOCAL_LLM_MODEL is unset")
    url = settings.local_llm_base_url.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    use_figure_name = getattr(settings, "image_prompts_include_figure_name", False)
    ctx = {"allow_figure_name": use_figure_name, "niche": niche}

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": first_user_msg},
    ]
    last_err: Exception | None = None

    for attempt in range(1, _MAX_RETRIES + 1):
        payload = {
            "model": settings.local_llm_model,
            "messages": messages,
            "response_format": {"type": "json_object"},
            "temperature": settings.local_llm_temperature,
            "max_tokens": settings.local_llm_max_tokens,
        }
        with httpx.Client(timeout=settings.local_llm_timeout_s) as c:
            try:
                r = c.post(url, headers=headers, json=payload)
            except httpx.RequestError as e:
                raise RuntimeError(
                    f"Local LLM connection error at {settings.local_llm_base_url}: {e}. "
                    "Is Ollama running? Try: ollama serve"
                ) from e
        if r.status_code >= 400:
            raise RuntimeError(
                f"Local LLM HTTP {r.status_code} ({settings.local_llm_model}): {r.text[:500]}"
            )
        raw = (r.json()["choices"][0]["message"]["content"] or "").strip()
        raw = _strip_think_blocks(raw)

        try:
            obj = _extract_json(raw)
            maybe_clamp_plan_json(obj)
            plan = NarrationPlan.model_validate(obj, context=ctx)
            return plan.model_dump(mode="json")
        except Exception as e:
            last_err = e
            if attempt == _MAX_RETRIES:
                break
            correction = _build_correction(obj if "obj" in dir() else {}, e)
            print(f"  [local] attempt {attempt} failed, retrying: {str(e)[:120]}")
            messages = [
                *messages,
                {"role": "assistant", "content": raw},
                {"role": "user", "content": correction},
            ]

    raise RuntimeError(
        f"local LLM ({settings.local_llm_model}) failed after {_MAX_RETRIES} attempts: {last_err}"
    )


def _build_correction(obj: dict, err: Exception) -> str:
    from shorts_pipeline.planner.client import _build_correction_message
    return _build_correction_message(obj, err)


# ────────────────────────────────────────────────────────────────────────────
# Used-topics tracker — never regenerate the same niche_progress topic twice
# ────────────────────────────────────────────────────────────────────────────

def _used_topics_path(out_dir: Path) -> Path:
    """Lives at scripts_out/_used_topics.json so all niches share one ledger."""
    return out_dir / "_used_topics.json"


def _load_used(p: Path) -> dict[str, list[str]]:
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_used(p: Path, data: dict[str, list[str]]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def _build_topic_prompt(t: dict) -> str:
    """Combine title + hook + keywords into the string the planner LLM sees."""
    parts = [t["title"]]
    if t.get("hook"):
        parts.append(f"\nNARRATOR HOOK (use as the opening line / framing angle):\n{t['hook']}")
    if t.get("keywords"):
        parts.append(f"\nKEYWORDS (weave into image prompts where natural): {', '.join(t['keywords'])}")
    if t.get("subcategory"):
        parts.append(f"\nSUBCATEGORY: {t['subcategory']}")
    return "\n".join(parts)


# ────────────────────────────────────────────────────────────────────────────
# One topic → validated plan dict
# ────────────────────────────────────────────────────────────────────────────

def generate_plan_for_topic(settings: Settings, niche_sys: str, niche_user_fn,
                            topic: str, *, niche: str | None = None,
                            backend: str = "auto") -> dict:
    use_figure_name = getattr(settings, "image_prompts_include_figure_name", False)
    try:
        base_user_prompt = niche_user_fn(topic, use_figure_name=use_figure_name)
    except TypeError:
        # Most niches' user_prompt takes only `topic` — the kwarg is history-only.
        base_user_prompt = niche_user_fn(topic)
    # Inject the niche TTS budget into BOTH system + user prompts. SYSTEM is the
    # strongest signal models obey — putting the cap there reduces retry count
    # dramatically. The user-prompt copy stays as a reminder near the topic spec.
    budget_block = _niche_budget_block(niche)
    full_system_prompt = budget_block + "\n" + niche_sys
    full_user_prompt = base_user_prompt + budget_block + _SCHEMA_PATCH

    # Backend selection: only local Qwen via Ollama is supported.  The
    # `backend` arg is preserved for CLI back-compat; any non-local value
    # is treated as local with a warning.
    backend = (backend or "local").lower().strip()
    if backend not in ("local", "auto", "ollama"):
        print(
            f"  [warn] --backend {backend!r} is no longer supported; using local Ollama",
            file=sys.stderr,
        )

    last_err: Exception | None = None
    try:
        return local_qwen_generate_with_retry(
            settings, full_system_prompt, full_user_prompt, niche=niche
        )
    except Exception as e:
        last_err = e
        print(f"  [local] all retries exhausted: {str(e)[:200]}", file=sys.stderr)

    raise RuntimeError(f"local backend failed for topic {topic!r}: {last_err}")


# ────────────────────────────────────────────────────────────────────────────
# CLI
# ────────────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("niche", help="niche module name, e.g. history, crime, military")
    ap.add_argument(
        "batch_path",
        nargs="?",
        default=None,
        help=(
            "folder with batch_*.json / 1.txt..N.txt / topics.txt OR a single .txt file. "
            "If omitted, defaults to ./topics/niches/<niche> "
            "(or ./topics/famous_people_1000 when niche=documentary)."
        ),
    )
    ap.add_argument("--out", default=str(ROOT / "scripts_out"),
                    help="output directory root (default: ./scripts_out)")
    ap.add_argument("--limit", type=int, default=0,
                    help="stop after N topics (0 = all)")
    ap.add_argument("--start", type=int, default=1,
                    help="skip topics with index < START (1-based)")
    ap.add_argument("--mode", choices=["normal", "long"], default="normal",
                    help="normal = 59-60 s TTS 1×; long = same script rendered at 0.9× TTS + 3 s outro")
    ap.add_argument(
        "--backend",
        choices=["local", "auto", "ollama"],
        default="local",
        help="planner backend (only local Ollama Qwen 3.6 27B is supported)",
    )
    args = ap.parse_args()

    settings = Settings()
    sys_prompt, user_fn = load_niche(args.niche)
    if args.batch_path:
        batch_path = Path(args.batch_path)
    elif args.niche == "documentary":
        batch_path = ROOT / "topics" / "famous_people_1000" / "batch_001"
    else:
        batch_path = ROOT / "topics" / "niches" / args.niche
    if not batch_path.exists():
        print(f"batch_path not found: {batch_path}", file=sys.stderr)
        return 1
    topics = load_topics(batch_path)
    if not topics:
        print(f"no topics found in {batch_path}", file=sys.stderr)
        return 1

    batch_name = batch_path.stem if batch_path.is_file() else batch_path.name
    out_dir_root = Path(args.out)
    out_root = out_dir_root / args.niche / batch_name
    out_root.mkdir(parents=True, exist_ok=True)

    used_path = _used_topics_path(out_dir_root)
    used = _load_used(used_path)
    used_set: set[str] = set(used.get(args.niche, []))

    rich = topics and isinstance(topics[0][1], dict)
    width = 4 if rich else 3

    print(f"niche={args.niche}  batch={batch_name}  topics={len(topics)}  "
          f"mode={args.mode}  backend={args.backend}  used={len(used_set)}  out={out_root}")

    done = 0
    for idx, topic in topics:
        if idx < args.start:
            continue
        if isinstance(topic, dict):
            unique_id = topic.get("unique_id")
            title = topic["title"]
            if unique_id and unique_id in used_set:
                continue  # silent skip — already rendered in a previous run
            topic_for_prompt = _build_topic_prompt(topic)
            slug_text = title
            display = title
        else:
            unique_id = None
            topic_for_prompt = topic
            slug_text = topic
            display = topic

        out_path = out_root / f"{idx:0{width}d}_{_slugify(slug_text)}.json"
        if out_path.exists():
            print(f"[{idx:0{width}d}] skip (exists): {display[:80]}")
            if unique_id:
                used_set.add(unique_id)
            continue

        print(f"[{idx:0{width}d}] {display[:80]}")
        t0 = time.time()
        try:
            plan = generate_plan_for_topic(settings, sys_prompt, user_fn,
                                           topic_for_prompt, niche=args.niche,
                                           backend=args.backend)
        except Exception as e:
            print(f"  FAILED: {e}", file=sys.stderr)
            continue
        plan["video_mode"] = args.mode
        plan["niche"] = args.niche
        if unique_id:
            plan["source_unique_id"] = unique_id
        out_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        print(f"  ok ({time.time() - t0:.1f}s) -> {out_path.name}")

        if unique_id:
            used_set.add(unique_id)
            used[args.niche] = sorted(used_set)
            _save_used(used_path, used)

        done += 1
        if args.limit and done >= args.limit:
            break

    if rich:
        used[args.niche] = sorted(used_set)
        _save_used(used_path, used)

    print(f"\ndone — {done} plan(s) written under {out_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
