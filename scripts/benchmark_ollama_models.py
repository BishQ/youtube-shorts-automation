"""Model benchmark: Ollama qwen3.5:9b vs Ollama gemma4:e4b vs LM Studio gemma — 5 niches × 5 topics.

All models run on the EXACT same (niche, topic) pairs under identical conditions.
Measures both script quality AND full visual output quality per plan.

VISUAL METRICS (per plan, 0–100 score):
  image_prompt  — avg length, lighting presence, shot-type vocabulary, human reference
  motion_prompt — fill rate (non-empty), avg length, camera-verb presence
  visual_tier   — how close to the 70/20/10 grounded/cinematic/legendary law
  beat variety  — unique emotions, cameras, transitions, audio events used
  intensity     — curve variance (flat=bad), has peak ≥0.8, has valley ≤0.4
  figure_present — mix of figure-in-frame vs environment shots

Usage:
  python scripts/benchmark_ollama_models.py
  python scripts/benchmark_ollama_models.py --topics-per-niche 3 --dry-run
  python scripts/benchmark_ollama_models.py --niches history,crime,science,military,cults

  # Custom models (name::url::structured_output_mode):
  python scripts/benchmark_ollama_models.py \\
      --models "qwen3.5:9b::http://localhost:11434/v1::json_object" \\
               "gemma4:e4b::http://localhost:11434/v1::json_object" \\
               "gemma-4-e4b::http://localhost:1234/v1::guided_json"
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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

from shorts_pipeline.config.settings import Settings  # noqa: E402
from shorts_pipeline.planner.cinematic import ARCHITECTURES, with_retries  # noqa: E402
from shorts_pipeline.planner.niche_caps import caps_for, count_syllables  # noqa: E402

MAIN = ARCHITECTURES["MAIN"]
OUT_DIR = ROOT / "scripts_out" / "model_bench"

DEFAULT_NICHES = ["history", "crime", "science", "military", "cults"]
TOPICS_PER_NICHE = 5
MAX_RETRIES = 3
TIMEOUT_S = 300.0

DEFAULT_MODELS = [
    "gemma-4-e4b::http://localhost:1234/v1::guided_json",
    "deepseek-chat::https://api.deepseek.com/v1::off",
]

# ── Visual scoring regexes (mirrors schema.py) ───────────────────────────────

_LIGHTING_RE = re.compile(
    r'(light|shadow|candle|backlit|silhouette|lamp|torch|dawn|dusk|overcast'
    r'|glow|gleam|dim|bright|flicker|sunlit|moonlit|spotlight|torchlit|haze|mist'
    r'|chiaroscuro|volumetric|fluorescent|diffused|ambient|sunrise|sunset'
    r'|twilight|midday|harsh|soft.light|natural.light|golden.hour|blue.hour)',
    re.IGNORECASE,
)
_SHOT_TYPE_RE = re.compile(
    r'(close.?up|wide shot|medium shot|low.angle|high.angle|dutch angle|overhead'
    r'|silhouette|tight|extreme|portrait shot|hero shot|cinematic portrait'
    r'|aerial|panoramic|tracking shot|establishing)',
    re.IGNORECASE,
)
_HUMAN_REF_RE = re.compile(
    r'(face|eye|eyes|hand|hands|figure|warrior|soldier|king|queen|emperor'
    r'|man|woman|crowd|people|portrait|fighter|general|rider|scholar)',
    re.IGNORECASE,
)
_CAMERA_VERB_RE = re.compile(
    r'(push.?in|pull.?back|dolly|pan|tilt|zoom|orbit|crane|track|drift|rise'
    r'|descend|rotate|handheld|micro.?shake|parallax|locked.?off|slow push)',
    re.IGNORECASE,
)
# Style tokens that "count" toward the 3-token-max visual density rule
_STYLE_TOKEN_RE = re.compile(
    r'(cinematic|epic|legendary|god.?ray|volumetric|particle|bokeh|film.?grain'
    r'|anamorphic|blockbuster|dramatic|hyper.?real|ultra.?detailed|masterpiece)',
    re.IGNORECASE,
)


# ── Visual scorer ─────────────────────────────────────────────────────────────

def score_visuals(plan: dict) -> dict:
    """Analyse all 11 visual fields. Returns a metrics dict with a 0–100 score."""
    clauses = plan.get("clauses", [])
    n = len(clauses)
    if n == 0:
        return {"visual_score": 0, "visual_error": "no clauses"}

    # ── Per-clause extraction ─────────────────────────────────────────────────
    img_lengths: list[int] = []
    mot_lengths: list[int] = []
    mot_filled: int = 0
    has_lighting: int = 0
    has_shot_type: int = 0
    has_human_ref: int = 0
    has_camera_verb: int = 0
    style_token_counts: list[int] = []
    tiers: dict[str, int] = {"grounded": 0, "cinematic": 0, "legendary": 0}
    emotions: set[str] = set()
    cameras: set[str] = set()
    transitions: set[str] = set()
    audio_events: set[str] = set()
    audio_non_none: int = 0
    intensities: list[float] = []
    figure_present_count: int = 0

    for c in clauses:
        img = c.get("image_prompt", "") or ""
        mot = c.get("motion_prompt", "") or ""
        beat = c.get("beat", {}) or {}

        img_lengths.append(len(img))
        mot_lengths.append(len(mot))
        if mot.strip():
            mot_filled += 1
        if _LIGHTING_RE.search(img):
            has_lighting += 1
        if _SHOT_TYPE_RE.search(img):
            has_shot_type += 1
        if _HUMAN_REF_RE.search(img):
            has_human_ref += 1
        if _CAMERA_VERB_RE.search(mot):
            has_camera_verb += 1
        style_token_counts.append(len(_STYLE_TOKEN_RE.findall(img)))

        tier = beat.get("visual_tier", "grounded")
        tiers[tier] = tiers.get(tier, 0) + 1

        em = beat.get("emotion", "")
        cam = beat.get("camera", "")
        tr = beat.get("transition_in", "")
        ae = beat.get("audio_event", "none")
        if em:
            emotions.add(em)
        if cam:
            cameras.add(cam)
        if tr:
            transitions.add(tr)
        if ae:
            audio_events.add(ae)
        if ae and ae != "none":
            audio_non_none += 1

        intensities.append(float(beat.get("intensity", 0.5)))
        if c.get("figure_present", True):
            figure_present_count += 1

    # ── Derived stats ─────────────────────────────────────────────────────────
    avg_img_len = sum(img_lengths) / n
    avg_mot_len = sum(mot_lengths) / n
    mot_fill_rate = mot_filled / n

    # Intensity curve
    mean_int = sum(intensities) / n
    variance_int = sum((x - mean_int) ** 2 for x in intensities) / n
    has_peak = any(x >= 0.8 for x in intensities)
    has_valley = any(x <= 0.4 for x in intensities)
    peak_val = max(intensities)
    valley_val = min(intensities)

    # Visual tier distribution vs ideal 70/20/10
    ideal = {"grounded": round(n * 0.7), "cinematic": round(n * 0.2), "legendary": round(n * 0.1)}
    tier_deviation = sum(abs(tiers.get(k, 0) - ideal[k]) for k in ideal)

    # Style-token density (max 3 per clause is the law)
    over_dense = sum(1 for t in style_token_counts if t > 3)
    avg_style_tokens = sum(style_token_counts) / n

    # Figure variety (at least 2 environment shots for scene breathing)
    figure_env_count = n - figure_present_count

    # ── Scoring (points out of 100) ───────────────────────────────────────────
    score = 0.0
    breakdown: dict[str, Any] = {}

    # image_prompt quality — 30 pts
    p_img_len = min(10.0, (avg_img_len / 120) * 10)           # 10pt: target ≥120 chars avg
    p_lighting = (has_lighting / n) * 8                        # 8pt: lighting in every clause
    p_shot = (has_shot_type / n) * 6                           # 6pt: shot type vocab
    p_human = (has_human_ref / n) * 6                          # 6pt: human/figure reference
    score += p_img_len + p_lighting + p_shot + p_human
    breakdown["img_avg_len"] = round(avg_img_len, 1)
    breakdown["img_lighting_rate"] = f"{has_lighting}/{n}"
    breakdown["img_shot_type_rate"] = f"{has_shot_type}/{n}"
    breakdown["img_human_ref_rate"] = f"{has_human_ref}/{n}"
    breakdown["img_pts"] = round(p_img_len + p_lighting + p_shot + p_human, 1)

    # motion_prompt quality — 20 pts
    p_mot_fill = mot_fill_rate * 10                            # 10pt: all filled
    p_mot_len = min(5.0, (avg_mot_len / 60) * 5)              # 5pt: target ≥60 chars avg
    p_cam_verb = (has_camera_verb / n) * 5                     # 5pt: camera verb present
    score += p_mot_fill + p_mot_len + p_cam_verb
    breakdown["mot_fill_rate"] = f"{mot_filled}/{n}"
    breakdown["mot_avg_len"] = round(avg_mot_len, 1)
    breakdown["mot_cam_verb_rate"] = f"{has_camera_verb}/{n}"
    breakdown["mot_pts"] = round(p_mot_fill + p_mot_len + p_cam_verb, 1)

    # visual_tier distribution — 15 pts (0 deviation = 15, each unit off costs 1.5)
    p_tier = max(0.0, 15.0 - tier_deviation * 1.5)
    score += p_tier
    breakdown["tiers"] = dict(tiers)
    breakdown["tier_deviation"] = tier_deviation
    breakdown["tier_pts"] = round(p_tier, 1)

    # beat variety — 20 pts
    p_emotion = min(10.0, len(emotions) * (10 / 7))            # 10pt: ≥7 unique emotions
    p_camera = min(4.0, len(cameras) * (4 / 4))                # 4pt: ≥4 unique cameras
    p_transition = min(3.0, len(transitions) * (3 / 4))        # 3pt: ≥4 unique transitions
    p_audio = min(3.0, audio_non_none * (3 / 6))               # 3pt: ≥6 non-none audio events
    score += p_emotion + p_camera + p_transition + p_audio
    breakdown["emotions"] = sorted(emotions)
    breakdown["cameras"] = sorted(cameras)
    breakdown["transitions"] = sorted(transitions)
    breakdown["audio_events"] = sorted(audio_events)
    breakdown["audio_non_none"] = audio_non_none
    breakdown["beat_pts"] = round(p_emotion + p_camera + p_transition + p_audio, 1)

    # intensity curve — 10 pts
    p_variance = min(5.0, (variance_int / 0.04) * 5)           # 5pt: variance ≥0.04 = full score
    p_peak = 3.0 if has_peak else 0.0                          # 3pt: has peak ≥0.8
    p_valley = 2.0 if has_valley else 0.0                      # 2pt: has valley ≤0.4
    score += p_variance + p_peak + p_valley
    breakdown["intensity_min"] = round(valley_val, 2)
    breakdown["intensity_max"] = round(peak_val, 2)
    breakdown["intensity_variance"] = round(variance_int, 4)
    breakdown["intensity_pts"] = round(p_variance + p_peak + p_valley, 1)

    # style token density — 5 pts (penalise over-dense prompts)
    p_density = max(0.0, 5.0 - over_dense * 1.5)              # 5pt: zero over-dense clauses
    score += p_density
    breakdown["avg_style_tokens"] = round(avg_style_tokens, 2)
    breakdown["over_dense_clauses"] = over_dense
    breakdown["density_pts"] = round(p_density, 1)

    breakdown["figure_present"] = f"{figure_present_count}/{n}"
    breakdown["figure_env"] = figure_env_count
    breakdown["visual_score"] = round(min(100.0, score), 1)

    return breakdown


# ── Model descriptor ──────────────────────────────────────────────────────────

@dataclass
class ModelCfg:
    name: str
    base_url: str
    structured_output: str
    label: str

    @staticmethod
    def parse(spec: str) -> "ModelCfg":
        parts = spec.split("::")
        if len(parts) < 2:
            raise ValueError(f"Bad model spec {spec!r} — format: name::url::mode")
        name = parts[0].strip()
        url = parts[1].strip().rstrip("/")
        mode = parts[2].strip() if len(parts) >= 3 else "json_object"
        label = (name.replace("gemma-4-", "lms-")
                     .replace("gemma4:", "oll-g4:")
                     .replace("qwen3.5:", "oll-q:")
                     .replace("deepseek-", "ds-"))
        return ModelCfg(name=name, base_url=url, structured_output=mode, label=label)

    def build_settings(self, timeout_s: float) -> Settings:
        s = Settings()
        s.local_llm_base_url = self.base_url
        s.local_llm_model = self.name
        s.local_llm_timeout_s = timeout_s
        s.local_llm_temperature = 0.4
        s.local_llm_planner_temperature = 0.05
        s.local_llm_structured_output = self.structured_output
        s.gpu_serial_mode = False
        return s

    def backend(self) -> str:
        if "11434" in self.base_url:
            return "Ollama"
        if "deepseek.com" in self.base_url:
            return "DeepSeek-API"
        if "1234" in self.base_url:
            return "LMStudio"
        return "Local"


# ── Topic loading ─────────────────────────────────────────────────────────────

def load_topics(niche: str, n: int) -> list[str]:
    folder = ROOT / "topics" / "niches" / niche
    if not folder.is_dir():
        raise FileNotFoundError(f"No topic folder: {folder}")
    topics: list[str] = []
    seen: set[str] = set()
    for bp in sorted(folder.glob("batch_*.json")):
        if len(topics) >= n:
            break
        try:
            arr = json.loads(bp.read_text(encoding="utf-8"))
        except Exception:
            continue
        for item in arr:
            if len(topics) >= n:
                break
            if isinstance(item, dict):
                title = (item.get("title") or "").strip()
            elif isinstance(item, str):
                title = item.strip()
            else:
                continue
            if title and title.lower() not in seen:
                seen.add(title.lower())
                topics.append(title)
    if not topics:
        raise ValueError(f"No topics found for niche '{niche}'")
    return topics[:n]


# ── Single plan run ───────────────────────────────────────────────────────────

def run_one(settings: Settings, *, model: str, niche: str, topic: str) -> dict[str, Any]:
    min_w, max_w, _ = caps_for(niche)
    row: dict[str, Any] = {"model": model, "niche": niche, "topic": topic, "pass": False}
    t0 = time.time()
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
        vis = score_visuals(plan)
        row.update(
            **{"pass": True},
            attempts=dbg.get("succeeded_on", 1),
            seconds=round(time.time() - t0, 1),
            words=w,
            syllables=count_syllables(full),
            in_band=min_w <= w <= max_w,
            clauses=len(plan.get("clauses", [])),
            visual_score=vis.get("visual_score", 0),
            visual=vis,
        )
    except Exception as exc:
        row.update(
            seconds=round(time.time() - t0, 1),
            error=str(exc)[:400],
            attempts=MAX_RETRIES,
        )
    return row


# ── Output helpers ────────────────────────────────────────────────────────────

def _append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _print_row(row: dict, label: str) -> None:
    status = "PASS" if row.get("pass") else "FAIL"
    if row.get("pass"):
        vscore = row.get("visual_score", "?")
        extra = (
            f"words={row.get('words')} in_band={row.get('in_band')} "
            f"clauses={row.get('clauses')} vis={vscore}/100 "
            f"attempt={row.get('attempts')}"
        )
    else:
        extra = (row.get("error") or "")[:90]
    print(
        f"  [{label:<16}] {row['niche']:<12} {status:4}  "
        f"{row.get('seconds', 0):6.1f}s  {extra}",
        flush=True,
    )


def _avgs(rows: list[dict], key: str) -> float:
    vals = [r.get(key, 0) for r in rows if r.get("pass") and r.get(key) is not None]
    return round(sum(vals) / len(vals), 1) if vals else 0.0


def _print_summary(results: list[dict], models: list[ModelCfg]) -> None:
    COL = 20

    def stats(rows: list[dict]) -> dict:
        if not rows:
            return {}
        passed = [r for r in rows if r.get("pass")]
        vis_rows = [r for r in passed if r.get("visual")]

        def va(key: str) -> float:
            vals = [r["visual"].get(key, 0) for r in vis_rows if isinstance(r["visual"].get(key), (int, float))]
            return round(sum(vals) / len(vals), 1) if vals else 0.0

        return {
            # ── script ───────────────────────────────────────────────────────
            "total": len(rows),
            "pass": len(passed),
            "fail": len(rows) - len(passed),
            "in_band": sum(1 for r in passed if r.get("in_band")),
            "avg_words": _avgs(passed, "words"),
            "avg_syl": _avgs(passed, "syllables"),
            "avg_sec": round(sum(r.get("seconds", 0) for r in rows) / len(rows), 1),
            "avg_attempts": round(sum(r.get("attempts", 1) for r in rows) / len(rows), 2),
            # ── visual ───────────────────────────────────────────────────────
            "vis_score": _avgs(passed, "visual_score"),
            "vis_img_len": va("img_avg_len"),
            "vis_img_pts": va("img_pts"),
            "vis_mot_fill": va("mot_pts"),
            "vis_tier_pts": va("tier_pts"),
            "vis_beat_pts": va("beat_pts"),
            "vis_intensity_pts": va("intensity_pts"),
            "vis_density_pts": va("density_pts"),
            "vis_audio_non_none": va("audio_non_none"),
        }

    model_stats = {m.name: stats([r for r in results if r["model"] == m.name]) for m in models}
    sep = "─" * (26 + COL * len(models))

    def section(title: str, rows: list[tuple[str, str]]) -> None:
        print(f"\n  {title}")
        print("  " + "─" * (24 + COL * len(models)))
        for key, label in rows:
            row_str = f"    {label:<22}"
            for m in models:
                val = model_stats[m.name].get(key, "-")
                row_str += f"{str(val):<{COL}}"
            print(row_str)

    print(f"\n{sep}")
    print("  BENCHMARK SUMMARY")
    print(sep)
    header = f"  {'Metric':<24}" + "".join(f"{m.label:<{COL}}" for m in models)
    print(header)

    section("SCRIPT QUALITY", [
        ("total",        "Topics run"),
        ("pass",         "Plans passed"),
        ("fail",         "Plans failed"),
        ("in_band",      "In word-band"),
        ("avg_words",    "Avg words"),
        ("avg_syl",      "Avg syllables"),
        ("avg_sec",      "Avg seconds/plan"),
        ("avg_attempts", "Avg attempts"),
    ])

    section("VISUAL QUALITY  (avg across passed plans)", [
        ("vis_score",         "TOTAL score /100"),
        ("vis_img_pts",       "  image_prompt /30"),
        ("vis_img_len",       "    avg img length"),
        ("vis_mot_fill",      "  motion_prompt /20"),
        ("vis_tier_pts",      "  visual_tier /15"),
        ("vis_beat_pts",      "  beat variety /20"),
        ("vis_intensity_pts", "  intensity curve /10"),
        ("vis_density_pts",   "  style density /5"),
        ("vis_audio_non_none","  audio events used"),
    ])

    print(f"\n{sep}")

    # ── Per-topic table ───────────────────────────────────────────────────────
    by_topic: dict[tuple[str, str], dict[str, dict]] = {}
    for r in results:
        by_topic.setdefault((r["niche"], r["topic"]), {})[r["model"]] = r

    print("\n  Per-topic  (PASS/FAIL  words  vis/100)")
    col_w = max(COL, 22)
    hdr = f"  {'Niche':<13} {'Topic':<34}" + "".join(f"{m.label:<{col_w}}" for m in models)
    print(hdr)
    print("  " + "─" * (13 + 34 + col_w * len(models)))

    for (niche, topic), by_model in sorted(by_topic.items()):
        def fmt(r: dict) -> str:
            if not r:
                return "N/A"
            if not r.get("pass"):
                return "FAIL"
            flag = "✓" if r.get("in_band") else "!"
            vs = r.get("visual_score", "?")
            return f"PASS{flag} {r.get('words','?')}w vis={vs}"

        row_str = f"  {niche:<13} {topic[:34]:<34}"
        for m in models:
            row_str += f"{fmt(by_model.get(m.name, {})):<{col_w}}"
        print(row_str)

    print(f"\n{sep}")
    print("  ✓=in word-band  !=out of band  vis=visual score /100")
    print(f"  Score breakdown: img/30 + motion/20 + tier/15 + beats/20 + intensity/10 + density/5")
    print(sep)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", nargs="+", default=DEFAULT_MODELS, metavar="SPEC")
    ap.add_argument("--niches", default=",".join(DEFAULT_NICHES))
    ap.add_argument("--topics-per-niche", type=int, default=TOPICS_PER_NICHE)
    ap.add_argument("--timeout", type=float, default=TIMEOUT_S)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    models: list[ModelCfg] = []
    for spec in args.models:
        try:
            models.append(ModelCfg.parse(spec))
        except ValueError as exc:
            print(f"[ERROR] {exc}", file=sys.stderr)
            return 1

    niches = [n.strip() for n in args.niches.split(",") if n.strip()]

    pairs: list[tuple[str, str]] = []
    for niche in niches:
        try:
            topics = load_topics(niche, args.topics_per_niche)
        except Exception as exc:
            print(f"[WARN] {niche}: {exc}", file=sys.stderr)
            continue
        for topic in topics:
            pairs.append((niche, topic))

    n_pairs = len(pairs)
    n_total = n_pairs * len(models)

    print(f"Model benchmark | niches={len(niches)} | pairs={n_pairs} | total_runs={n_total}", flush=True)
    for i, m in enumerate(models, 1):
        print(f"  Model {i}: {m.name}  [{m.backend()}]  {m.base_url}  mode={m.structured_output}", flush=True)
    print(flush=True)

    if args.dry_run:
        for niche, topic in pairs:
            min_w, max_w, _ = caps_for(niche)
            print(f"  {niche:<14} {topic[:55]}  (words {min_w}–{max_w})")
        print(f"\n[dry-run] Would run {n_total} LLM calls — exiting.")
        return 0

    model_settings = {m.name: m.build_settings(args.timeout) for m in models}

    ts = time.strftime("%Y%m%d_%H%M%S", time.localtime())
    log_path = OUT_DIR / f"bench_{ts}.jsonl"
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    results: list[dict] = []
    run_num = 0

    for niche, topic in pairs:
        print(f"\n=== {niche} :: {topic[:65]} ===", flush=True)
        for m in models:
            run_num += 1
            print(f"  [{run_num}/{n_total}] {m.label} ...", end="", flush=True)
            row = run_one(model_settings[m.name], model=m.name, niche=niche, topic=topic)
            row["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            row["run_num"] = run_num
            row["backend"] = m.backend()
            results.append(row)
            _append_jsonl(log_path, row)
            print("", flush=True)
            _print_row(row, m.label)

    _print_summary(results, models)

    summary_path = OUT_DIR / f"summary_{ts}.json"
    summary_path.write_text(
        json.dumps(
            {
                "models": [{"name": m.name, "backend": m.backend(), "url": m.base_url} for m in models],
                "niches": niches,
                "topics_per_niche": args.topics_per_niche,
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nLog:     {log_path}")
    print(f"Summary: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
