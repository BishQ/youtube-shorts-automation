"""Detailed Q4 vs Q6 comparison for the same niche/idx pairs.

Outputs:
  - scripts_out/q4_vs_q6_compare.md  — side-by-side narratives + image + motion
  - scripts_out/q4_vs_q6_stats.txt   — aggregate quality metrics

Quality scoring (heuristic, per generation):
  IMAGE prompts:
    + has shot-type word
    + has lighting word
    + has period prop / palette mention
    + length 80-200 chars (rich but not bloated)
    + face/expression / posture mentioned (human visible)
    - banned words / generic ("a person", "a place")
  MOTION prompts:
    + starts with subject verb (lowers, raises, turns, opens, etc.)
    + mentions environment beat (smoke, dust, light, banner, etc.)
    + length 40-120 chars
    + unique vs others in same plan
    - just camera move ("camera pans", no subject action)
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "scripts_out" / "gemma_volume"
OUT_MD = ROOT / "scripts_out" / "q4_vs_q6_compare.md"
OUT_TXT = ROOT / "scripts_out" / "q4_vs_q6_stats.txt"


SHOT_WORDS = {"close-up", "wide shot", "medium shot", "low-angle", "high-angle",
              "establishing", "hero portrait", "extreme close-up", "tracking shot",
              "top-down", "over-the-shoulder", "profile silhouette", "aerial shot"}
LIGHT_WORDS = {"candle", "torch", "dawn", "dusk", "glow", "dim", "sunlit", "moonlit",
               "backlit", "torchlit", "chiaroscuro", "tungsten", "golden hour",
               "blue hour", "flicker", "haze", "mist", "soft light", "natural light",
               "twilight", "spotlight", "sunrise", "sunset", "lamp"}
ACTION_VERBS = {"raises", "raise", "lowers", "lower", "turns", "turn", "opens", "open",
                "closes", "close", "pours", "pour", "lifts", "lift", "drops", "drop",
                "draws", "draw", "pulls", "pull", "pushes", "push", "signs", "sign",
                "seals", "seal", "kneels", "kneel", "walks", "walk", "reaches", "reach",
                "grips", "grip", "releases", "release", "drinks", "drink", "exhales",
                "exhale", "blinks", "blink", "smiles", "smile", "weeps", "weep",
                "points", "point", "presses", "press", "traces", "trace", "leans",
                "lean", "stands", "stand", "sits", "sit", "looks", "look"}
ENV_WORDS = {"smoke", "dust", "light", "banner", "flag", "wind", "rain", "fire",
             "shadow", "cloth", "fog", "mist", "ember", "ash", "flame", "steam",
             "curls", "drifts", "ripples", "flickers", "glints", "glimmers"}
FACE_WORDS = {"face", "eye", "eyes", "jaw", "brow", "lips", "mouth", "stare",
              "expression", "gaze", "frown", "smile", "tear"}
BANNED = {"skyscraper", "smartphone", "laptop", "cell phone", "neon sign"}


def _score_image(text: str) -> dict:
    t = text.lower()
    s = {
        "shot": int(any(w in t for w in SHOT_WORDS)),
        "light": int(any(w in t for w in LIGHT_WORDS)),
        "face": int(any(w in t for w in FACE_WORDS)),
        "length_ok": int(80 <= len(text) <= 250),
        "banned": int(any(w in t for w in BANNED)),
        "chars": len(text),
    }
    s["score"] = s["shot"] + s["light"] + s["face"] + s["length_ok"] - 2*s["banned"]
    return s


def _score_motion(text: str, others: list[str]) -> dict:
    t = text.lower()
    s = {
        "action_verb": int(any(re.search(rf"\b{v}\b", t) for v in ACTION_VERBS)),
        "env": int(any(w in t for w in ENV_WORDS)),
        "length_ok": int(40 <= len(text) <= 150),
        "unique": int(text.strip().lower() not in [o.strip().lower() for o in others if o != text]),
        "chars": len(text),
    }
    s["score"] = s["action_verb"] + s["env"] + s["length_ok"] + s["unique"]
    return s


def _load_plans(competitor: str) -> dict:
    """Returns dict[(niche, idx)] = plan."""
    out = {}
    for jf in (SRC / competitor).glob("*/*.json"):
        try:
            d = json.loads(jf.read_text(encoding="utf-8"))
        except Exception: continue
        m = d.get("_meta", {})
        if not m.get("success"): continue
        plan = d.get("plan")
        if not plan: continue
        out[(m.get("niche"), m.get("idx"))] = (plan, m)
    return out


def main() -> int:
    q4 = _load_plans("gemma-q4")
    q6 = _load_plans("gemma-q6")
    common = sorted(set(q4) & set(q6))

    md_lines = ["# Gemma Q4 vs Q6 — Detailed Comparison\n"]
    md_lines.append(f"**Pairs compared**: {len(common)}\n")
    md_lines.append(f"Q4 total: {len(q4)}  |  Q6 total: {len(q6)}\n\n---\n")

    # Aggregate stats
    img_scores = {"q4": [], "q6": []}
    mot_scores = {"q4": [], "q6": []}
    image_total_chars = {"q4": 0, "q6": 0}
    motion_total_chars = {"q4": 0, "q6": 0}
    time_taken = {"q4": [], "q6": []}

    for (niche, idx) in common:
        plan_q4, meta_q4 = q4[(niche, idx)]
        plan_q6, meta_q6 = q6[(niche, idx)]
        topic = meta_q4.get("topic", "?")
        md_lines.append(f"\n## {niche.upper()} #{idx} — {topic[:80]}\n")
        md_lines.append(f"_Q4_: {meta_q4.get('words')}w, {meta_q4.get('total_time_s', meta_q4.get('time_s', 0))}s | _Q6_: {meta_q6.get('words')}w, {meta_q6.get('total_time_s', meta_q6.get('time_s', 0))}s\n")
        time_taken["q4"].append(meta_q4.get("total_time_s", 0))
        time_taken["q6"].append(meta_q6.get("total_time_s", 0))

        # Narration comparison
        md_lines.append("### Narration (first 4 clauses)\n")
        md_lines.append("| # | Q4 | Q6 |\n|---|---|---|\n")
        for i in range(min(4, len(plan_q4.get("clauses", [])))):
            t4 = plan_q4["clauses"][i].get("text", "").replace("\n", " ")[:160]
            t6 = plan_q6["clauses"][i].get("text", "").replace("\n", " ")[:160]
            md_lines.append(f"| {i+1} | {t4} | {t6} |\n")

        # Image scoring
        img_clauses_q4 = plan_q4.get("clauses", [])
        img_clauses_q6 = plan_q6.get("clauses", [])
        q4_img_scores = [_score_image(c.get("image_prompt", "")) for c in img_clauses_q4]
        q6_img_scores = [_score_image(c.get("image_prompt", "")) for c in img_clauses_q6]
        img_avg_q4 = mean(s["score"] for s in q4_img_scores) if q4_img_scores else 0
        img_avg_q6 = mean(s["score"] for s in q6_img_scores) if q6_img_scores else 0
        img_chars_q4 = mean(s["chars"] for s in q4_img_scores) if q4_img_scores else 0
        img_chars_q6 = mean(s["chars"] for s in q6_img_scores) if q6_img_scores else 0
        img_scores["q4"].append(img_avg_q4)
        img_scores["q6"].append(img_avg_q6)
        image_total_chars["q4"] += int(img_chars_q4)
        image_total_chars["q6"] += int(img_chars_q6)

        md_lines.append(f"\n### Image prompt — avg score Q4: **{img_avg_q4:.2f}/4** ({img_chars_q4:.0f} chars) | Q6: **{img_avg_q6:.2f}/4** ({img_chars_q6:.0f} chars)\n")
        md_lines.append("Sample (clause 1):\n")
        md_lines.append(f"- **Q4**: {img_clauses_q4[0].get('image_prompt','')[:300]}\n")
        md_lines.append(f"- **Q6**: {img_clauses_q6[0].get('image_prompt','')[:300]}\n")

        # Motion scoring
        q4_motions = [c.get("motion_prompt", "") for c in img_clauses_q4]
        q6_motions = [c.get("motion_prompt", "") for c in img_clauses_q6]
        q4_mot_scores = [_score_motion(m, q4_motions) for m in q4_motions]
        q6_mot_scores = [_score_motion(m, q6_motions) for m in q6_motions]
        mot_avg_q4 = mean(s["score"] for s in q4_mot_scores) if q4_mot_scores else 0
        mot_avg_q6 = mean(s["score"] for s in q6_mot_scores) if q6_mot_scores else 0
        mot_chars_q4 = mean(s["chars"] for s in q4_mot_scores) if q4_mot_scores else 0
        mot_chars_q6 = mean(s["chars"] for s in q6_mot_scores) if q6_mot_scores else 0
        mot_scores["q4"].append(mot_avg_q4)
        mot_scores["q6"].append(mot_avg_q6)
        motion_total_chars["q4"] += int(mot_chars_q4)
        motion_total_chars["q6"] += int(mot_chars_q6)

        md_lines.append(f"\n### Motion prompt — avg score Q4: **{mot_avg_q4:.2f}/4** ({mot_chars_q4:.0f} chars) | Q6: **{mot_avg_q6:.2f}/4** ({mot_chars_q6:.0f} chars)\n")
        md_lines.append("Sample (clause 1):\n")
        md_lines.append(f"- **Q4**: {q4_motions[0][:300]}\n")
        md_lines.append(f"- **Q6**: {q6_motions[0][:300]}\n")

    # Aggregate
    txt = []
    txt.append("=" * 60)
    txt.append("GEMMA Q4 vs Q6 — AGGREGATE STATS")
    txt.append("=" * 60)
    txt.append(f"Common pairs: {len(common)}")
    txt.append("")
    if img_scores["q4"]:
        txt.append(f"Image prompt avg score (out of 4):")
        txt.append(f"  Q4: {mean(img_scores['q4']):.2f}")
        txt.append(f"  Q6: {mean(img_scores['q6']):.2f}")
        txt.append(f"  → Winner: {'Q4' if mean(img_scores['q4']) > mean(img_scores['q6']) else 'Q6' if mean(img_scores['q6']) > mean(img_scores['q4']) else 'TIE'}")
        txt.append("")
        txt.append(f"Image prompt avg length (chars):")
        txt.append(f"  Q4: {image_total_chars['q4']//len(common)}")
        txt.append(f"  Q6: {image_total_chars['q6']//len(common)}")
        txt.append(f"  → More detailed: {'Q4' if image_total_chars['q4'] > image_total_chars['q6'] else 'Q6'}")
        txt.append("")
    if mot_scores["q4"]:
        txt.append(f"Motion prompt avg score (out of 4):")
        txt.append(f"  Q4: {mean(mot_scores['q4']):.2f}")
        txt.append(f"  Q6: {mean(mot_scores['q6']):.2f}")
        txt.append(f"  → Winner: {'Q4' if mean(mot_scores['q4']) > mean(mot_scores['q6']) else 'Q6' if mean(mot_scores['q6']) > mean(mot_scores['q4']) else 'TIE'}")
        txt.append("")
        txt.append(f"Motion prompt avg length (chars):")
        txt.append(f"  Q4: {motion_total_chars['q4']//len(common)}")
        txt.append(f"  Q6: {motion_total_chars['q6']//len(common)}")
        txt.append("")
    if time_taken["q4"]:
        txt.append(f"Avg generation time:")
        txt.append(f"  Q4: {mean(time_taken['q4']):.1f}s")
        txt.append(f"  Q6: {mean(time_taken['q6']):.1f}s")
        txt.append(f"  → Faster: {'Q4' if mean(time_taken['q4']) < mean(time_taken['q6']) else 'Q6'}  ({abs(mean(time_taken['q6']) - mean(time_taken['q4']))/mean(time_taken['q4'])*100:.0f}% diff)")

    txt_str = "\n".join(txt)

    OUT_MD.write_text("".join(md_lines), encoding="utf-8")
    OUT_TXT.write_text(txt_str, encoding="utf-8")
    print(txt_str)
    print()
    print(f"Markdown report: {OUT_MD}")
    print(f"Stats:           {OUT_TXT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
