"""Per-niche calibration analysis.

Reads calibration_results.csv, groups by niche, and computes:
  • niche-level mean/std of sps, wps, avg_syllables
  • global cross-niche regression: duration vs syllables (already R²≈0.86 univariate)
  • per-niche worst-case sps (used to derive safe syllable cap for 58 s target)
  • recommended per-niche MAX_WORDS = floor(MAX_SYLLABLES / avg_syllables_per_word)

Prints a markdown table ready to paste into the planner schema docs.
"""

from __future__ import annotations

import csv
from pathlib import Path
from statistics import mean, stdev

ROOT = Path(__file__).resolve().parent
CSV_PATH = ROOT / "calibration_results.csv"

TARGET_SEC = 58.0       # body budget; outro takes ~1 s of the 60 s cap
SAFETY_FRACTION = 0.95  # leave ~5% headroom for TTS variance


def load_rows():
    with CSV_PATH.open(encoding="utf-8") as f:
        return [
            {
                "niche": row["niche"],
                "topic": row["topic"],
                "words": int(row["words"]),
                "syllables": int(row["syllables"]),
                "avg_syl": float(row["avg_syllables"]),
                "polysyl_ratio": float(row["polysyl_ratio"]),
                "proper_ratio": float(row["proper_noun_ratio"]),
                "terminators": int(row["terminators"]),
                "very_long": int(row["very_long_words"]),
                "duration": float(row["duration"]),
                "wps": float(row["wps"]),
                "sps": float(row["sps"]),
            }
            for row in csv.DictReader(f)
        ]


def main():
    rows = load_rows()
    print(f"sample size: {len(rows)} across {len(set(r['niche'] for r in rows))} niches\n")

    # Global model: duration = a * syllables (slope-only zero-intercept gives sps_inv)
    syls = [r["syllables"] for r in rows]
    durs = [r["duration"] for r in rows]
    spss = [r["sps"] for r in rows]
    print("Global sps distribution:")
    print(f"  mean={mean(spss):.2f}  stdev={stdev(spss):.2f}")
    print(f"  min={min(spss):.2f}    max={max(spss):.2f}")
    print(f"  5th-pct (worst-case)={sorted(spss)[len(spss)//20]:.2f}")
    print()

    # Group by niche
    by_niche: dict[str, list[dict]] = {}
    for r in rows:
        by_niche.setdefault(r["niche"], []).append(r)

    print("Per-niche table (sorted by mean sps, slowest first):")
    print()
    print(f"{'niche':<14} {'n':>2}  {'sps mean':>8} {'sps min':>7} {'avSyl':>5}  "
          f"{'poly%':>5} {'PN%':>4}  {'reco MAX_SYL':>12} {'reco MAX_WORDS':>14}")
    print("-" * 100)

    niche_caps = {}
    rows_md = []

    # Sort niches by mean sps ascending (slowest first → tightest budget)
    sorted_niches = sorted(by_niche.items(), key=lambda kv: mean(r["sps"] for r in kv[1]))

    for niche, niche_rows in sorted_niches:
        n = len(niche_rows)
        m_sps = mean(r["sps"] for r in niche_rows)
        min_sps = min(r["sps"] for r in niche_rows)
        m_avsyl = mean(r["avg_syl"] for r in niche_rows)
        m_poly = mean(r["polysyl_ratio"] for r in niche_rows) * 100
        m_pn = mean(r["proper_ratio"] for r in niche_rows) * 100

        # Cap from worst observed sps in this niche, applied to 58 s × 0.95 safety
        worst_case_sps = min_sps
        max_syl = int(TARGET_SEC * worst_case_sps * SAFETY_FRACTION)
        max_words = int(max_syl / m_avsyl)

        niche_caps[niche] = {
            "max_syllables": max_syl,
            "max_words": max_words,
            "avg_syl_per_word": round(m_avsyl, 3),
            "worst_sps": round(worst_case_sps, 3),
            "mean_sps": round(m_sps, 3),
        }

        print(f"{niche:<14} {n:>2}  {m_sps:>8.2f} {min_sps:>7.2f} {m_avsyl:>5.2f}  "
              f"{m_poly:>4.1f}% {m_pn:>3.1f}%  {max_syl:>12d} {max_words:>14d}")

        rows_md.append((niche, m_sps, min_sps, m_avsyl, max_syl, max_words))

    print()
    print("Recommended per-niche caps (JSON form for planner config):")
    import json
    print(json.dumps(niche_caps, indent=2))

    # Write to disk
    out = ROOT / "niche_caps.json"
    out.write_text(json.dumps(niche_caps, indent=2), encoding="utf-8")
    print(f"\nwrote: {out}")


if __name__ == "__main__":
    main()
