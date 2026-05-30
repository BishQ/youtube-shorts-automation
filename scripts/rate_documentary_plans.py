"""Rate all documentary plan.json under data/jobs/."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from shorts_pipeline.planner.cinematic._shared import contains_ai_phrases
from shorts_pipeline.planner.niche_caps import caps_for, count_syllables

MIN_W, MAX_W, MAX_SYL = caps_for("documentary")
JOBS = ROOT / "data" / "jobs"
SHOT_RE = re.compile(
    r"close-up|wide shot|medium shot|low-angle|high-angle|establishing|"
    r"portrait|aerial|over-the-shoulder|hero shot|tracking|top-down",
    re.I,
)


def score_plan(d: dict) -> dict:
    full = d.get("full_script", "")
    w = len(full.split())
    syl = count_syllables(full)
    clauses = d.get("clauses") or []
    notes: list[str] = []

    tech = 10.0
    if w < MIN_W:
        tech -= 2
        notes.append("short")
    elif w > MAX_W:
        tech -= 1.5
        notes.append("long")
    if syl > MAX_SYL:
        tech -= min(3.0, (syl - MAX_SYL) / 30)
        notes.append("high syllables")
    from shorts_pipeline.planner.schema import CLAUSE_COUNT

    if len(clauses) != CLAUSE_COUNT:
        tech -= 4
        notes.append("clause count")
    extra_q = sum(1 for i, c in enumerate(clauses) if i > 0 and "?" in (c.get("text") or ""))
    if extra_q:
        tech -= min(2.0, extra_q * 0.5)
        notes.append(f"{extra_q} extra ?")

    narr = 7.5
    c1 = (clauses[0].get("text") if clauses else "") or ""
    if "?" not in c1[:80]:
        narr -= 1
        notes.append("weak hook")
    if contains_ai_phrases(full):
        narr -= 1.5
        notes.append("AI phrases")
    fig = (d.get("historical_figure") or "").split()[0].lower()
    early = " ".join(clauses[i].get("text", "") for i in range(min(3, len(clauses)))).lower()
    if fig and len(fig) > 3 and fig in early:
        narr -= 0.5
        notes.append("name too early")

    vis = 8.0
    no_shot = sum(1 for c in clauses if not SHOT_RE.search(c.get("image_prompt") or ""))
    if no_shot:
        vis -= min(2.0, no_shot * 0.25)
        notes.append(f"{no_shot} img no shot type")
    mot = {(c.get("motion_prompt") or "").strip().lower() for c in clauses}
    if len(mot) < 12:
        vis -= 0.5

    overall = max(1.0, min(10.0, 0.4 * tech + 0.35 * narr + 0.25 * vis))
    return {
        "overall": round(overall, 1),
        "tech": round(tech, 1),
        "narr": round(narr, 1),
        "vis": round(vis, 1),
        "words": w,
        "syl": syl,
        "extra_q": extra_q,
        "notes": notes,
    }


def main() -> None:
    rated: list[dict] = []
    for p in sorted(JOBS.glob("*/plan.json"), key=lambda x: x.stat().st_mtime):
        d = json.loads(p.read_text(encoding="utf-8"))
        if d.get("niche") != "documentary" and "source_index" not in d:
            continue
        fig = d.get("historical_figure") or p.parent.name
        s = score_plan(d)
        rated.append({"figure": fig, "job": p.parent.name, **s})

    rated.sort(key=lambda x: -x["overall"])
    print(f"Rated: {len(rated)} documentary plans")
    print(f"Avg score: {mean(r['overall'] for r in rated):.1f}/10")
    print(f"8+: {sum(1 for r in rated if r['overall'] >= 8)}")
    print(f"6-7.9: {sum(1 for r in rated if 6 <= r['overall'] < 8)}")
    print(f"<6: {sum(1 for r in rated if r['overall'] < 6)}")
    print("\nTOP 10:")
    for r in rated[:10]:
        n = "; ".join(r["notes"][:2]) or "ok"
        print(f"  {r['overall']}/10  {r['figure'][:40]:40}  w={r['words']}  {n}")
    print("\nBOTTOM 10:")
    for r in rated[-10:]:
        n = "; ".join(r["notes"][:3]) or "ok"
        print(f"  {r['overall']}/10  {r['figure'][:40]:40}  w={r['words']}  {n}")

    out = ROOT / "scripts_out" / "documentary_gemma_plans" / "_ratings.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rated, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nFull list: {out}")


if __name__ == "__main__":
    main()
