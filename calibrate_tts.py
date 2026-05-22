"""TTS pacing calibrator.

Analyse existing (plan.json, narration.wav) pairs on disk to find which
TEXT features predict Kokoro's words-per-second rate. Output:

  • A per-job table: words, periods, polysyllabic-word ratio, proper-noun
    ratio, capitalised-mid-sentence ratio, duration, measured wps.
  • A regression of duration on (words, terminators, polysyllabic_count,
    proper_noun_count) so we can predict TTS length WITHOUT rendering.
  • A "safe word budget" recommendation for a 58 s target given the
    measured worst-case wps in each complexity tier.

This script reads files only — no LLM calls, no TTS renders. Run it
before deciding whether to spend API budget on per-niche generation.

Usage:
    python calibrate_tts.py
    python calibrate_tts.py --include-trash
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from statistics import mean, stdev

ROOT = Path(__file__).resolve().parent
JOBS = ROOT / "data" / "jobs"

VOWEL_GROUP = re.compile(r"[aeiouyAEIOUY]+")
WORD_RE = re.compile(r"[A-Za-z][A-Za-z'-]*")


def syllables(word: str) -> int:
    """Cheap syllable estimate: count vowel groups, min 1."""
    w = word.strip("-'")
    if not w:
        return 0
    return max(1, len(VOWEL_GROUP.findall(w)))


def probe_duration(wav: Path) -> float:
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(wav)],
        text=True,
    )
    return float(out.strip())


def analyse_text(text: str) -> dict[str, float]:
    words = WORD_RE.findall(text)
    n = len(words) or 1
    syls = [syllables(w) for w in words]
    polysyl = sum(1 for s in syls if s >= 3)
    # Proper-noun proxy: capitalised words NOT at sentence start.
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    proper = 0
    for s in sentences:
        ws = WORD_RE.findall(s)
        for i, w in enumerate(ws):
            if i == 0:
                continue
            if w[0].isupper() and w.lower() != "i":
                proper += 1
    terminators = sum(text.count(c) for c in ".!?")
    # Very-long words (≥10 chars) are extra-slow in Kokoro.
    very_long = sum(1 for w in words if len(w) >= 10)
    return {
        "words": n,
        "syllables": sum(syls),
        "avg_syllables": sum(syls) / n,
        "polysyl_count": polysyl,
        "polysyl_ratio": polysyl / n,
        "proper_noun_count": proper,
        "proper_noun_ratio": proper / n,
        "terminators": terminators,
        "very_long_words": very_long,
    }


def collect(include_trash: bool):
    rows = []
    for plan_path in JOBS.rglob("plan.json"):
        if "trash" in plan_path.parts and not include_trash:
            continue
        wav = plan_path.parent / "narration.wav"
        if not wav.exists():
            continue
        try:
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        text = (plan.get("full_script") or "").strip()
        if not text:
            continue
        try:
            dur = probe_duration(wav)
        except Exception:
            continue
        feats = analyse_text(text)
        wps = feats["words"] / dur
        # Effective pacing: how long per syllable (TTS unit). More stable across topics.
        sps = feats["syllables"] / dur
        rows.append({
            "job": str(plan_path.parent.relative_to(ROOT)),
            "duration": dur,
            "wps": wps,
            "sps": sps,
            **feats,
        })
    return rows


def linfit(xs, ys):
    """Return (slope, intercept) for least-squares y = m*x + b."""
    n = len(xs)
    if n < 2:
        return 0.0, 0.0
    mx, my = mean(xs), mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = sum((x - mx) ** 2 for x in xs)
    if den == 0:
        return 0.0, my
    m = num / den
    b = my - m * mx
    return m, b


def r_squared(xs, ys, m, b):
    if not xs:
        return 0.0
    my = mean(ys)
    ss_tot = sum((y - my) ** 2 for y in ys)
    ss_res = sum((y - (m * x + b)) ** 2 for x, y in zip(xs, ys))
    return 1 - ss_res / ss_tot if ss_tot else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--include-trash", action="store_true")
    args = ap.parse_args()

    rows = collect(include_trash=args.include_trash)
    if not rows:
        print("no (plan.json, narration.wav) pairs found.")
        return

    rows.sort(key=lambda r: r["wps"])

    # Header
    hdr = f"{'job':<48} {'wrd':>4} {'dur':>5} {'wps':>5} {'sps':>5} {'avSyl':>5} {'poly%':>5} {'PN%':>5} {'term':>4} {'lng':>4}"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        print(
            f"{r['job'][-48:]:<48} {r['words']:>4} {r['duration']:>5.1f} "
            f"{r['wps']:>5.2f} {r['sps']:>5.2f} {r['avg_syllables']:>5.2f} "
            f"{r['polysyl_ratio']*100:>4.0f}% {r['proper_noun_ratio']*100:>4.0f}% "
            f"{int(r['terminators']):>4d} {int(r['very_long_words']):>4d}"
        )

    print()
    print(f"sample size: {len(rows)}")
    durs = [r["duration"] for r in rows]
    wpss = [r["wps"] for r in rows]
    print(f"duration: mean={mean(durs):.2f}s  min={min(durs):.2f}s  max={max(durs):.2f}s")
    print(f"wps:      mean={mean(wpss):.2f}  min={min(wpss):.2f}  max={max(wpss):.2f}  "
          f"stdev={stdev(wpss):.2f}" if len(wpss) > 1 else "")

    # Univariate regressions: duration vs each feature
    print("\nDuration vs single features (R²):")
    for feat in ("words", "syllables", "polysyl_count", "proper_noun_count",
                 "terminators", "very_long_words"):
        xs = [r[feat] for r in rows]
        ys = durs
        m, b = linfit(xs, ys)
        r2 = r_squared(xs, ys, m, b)
        print(f"  duration = {m:+.4f} × {feat:<20s} + {b:+.2f}    R²={r2:.3f}")

    # Combined: duration ~= a*syllables + b*terminators (assumed dominant)
    # Fit a × syllables only first; residual fit terminator coeff.
    syls = [r["syllables"] for r in rows]
    m1, b1 = linfit(syls, durs)
    resid = [d - (m1 * s + b1) for d, s in zip(durs, syls)]
    term = [r["terminators"] for r in rows]
    m2, b2 = linfit(term, resid)
    r2_combined = r_squared(
        list(range(len(rows))),
        durs,
        0,
        0,
    )  # placeholder
    # Compute true combined R²
    pred = [m1 * s + b1 + m2 * t + b2 for s, t in zip(syls, term)]
    my = mean(durs)
    ss_tot = sum((y - my) ** 2 for y in durs)
    ss_res = sum((y - p) ** 2 for y, p in zip(durs, pred))
    r2_combined = 1 - ss_res / ss_tot if ss_tot else 0.0

    print(f"\nCombined fit: duration ~= {m1:.4f}·syllables + {m2:.4f}·terminators + {b1+b2:+.2f}")
    print(f"Combined R²:  {r2_combined:.3f}")

    # Budget recommendations
    print("\nSafe word-budget recommendations for 58 s target:")
    # Worst case wps in observed set (excluding outliers? show both)
    worst = min(wpss)
    median_wps = sorted(wpss)[len(wpss) // 2]
    p25 = sorted(wpss)[max(0, len(wpss) // 4)]
    print(f"  Using worst observed wps   ({worst:.2f}): max ~= {int(58 * worst):>3d} words")
    print(f"  Using 25th-pct wps         ({p25:.2f}): max ~= {int(58 * p25):>3d} words")
    print(f"  Using median wps           ({median_wps:.2f}): max ~= {int(58 * median_wps):>3d} words")
    print("  -> recommend cap = floor(58 × 25th-pct wps) for safety on technical topics.")


if __name__ == "__main__":
    main()
