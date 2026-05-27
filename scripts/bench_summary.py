"""Live summary of LM Studio benchmark.

Reads scripts_out/bench/<model>/<niche>__<idx>.json files and prints
per-model + per-niche + overall stats with all metrics.

Run:  python scripts/bench_summary.py
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "scripts_out" / "bench"


def _read_all() -> list[dict]:
    rows = []
    for jf in BENCH.glob("*/*.json"):
        try:
            d = json.loads(jf.read_text(encoding="utf-8"))
            meta = d.get("_meta", {})
            rows.append(meta)
        except Exception:
            pass
    return rows


def _fmt_pct(num: int, denom: int) -> str:
    return f"{100*num/denom:.0f}%" if denom else "—"


def _stat_block(rows: list[dict], label: str) -> str:
    if not rows:
        return f"  {label}: no data\n"
    total = len(rows)
    succ = [r for r in rows if r.get("success")]
    fail = [r for r in rows if not r.get("success")]
    times = [r["time_s"] for r in rows if isinstance(r.get("time_s"), (int, float))]
    attempts = [r.get("attempts", 0) for r in rows if r.get("attempts")]
    succ_attempts = [r.get("attempts", 0) for r in succ if r.get("attempts")]
    in_band = [r for r in succ if r.get("in_band")]
    err_types = Counter(r.get("error_type") for r in fail if r.get("error_type"))
    words_succ = [r["words"] for r in succ if r.get("words")]
    syl_succ = [r["syllables"] for r in succ if r.get("syllables")]

    lines = [f"  {label}: {total} runs"]
    lines.append(f"    success: {len(succ)} ({_fmt_pct(len(succ), total)})   "
                 f"in-band: {len(in_band)} ({_fmt_pct(len(in_band), len(succ))} of success)")
    if times:
        lines.append(f"    time_s: avg={mean(times):.1f}  median={median(times):.1f}  "
                     f"min={min(times):.1f}  max={max(times):.1f}")
    if attempts:
        lines.append(f"    attempts (overall): avg={mean(attempts):.2f}  max={max(attempts)}  "
                     f"first-try-success: {sum(1 for r in succ if r.get('attempts')==1)}/{len(succ)}")
    if succ_attempts:
        lines.append(f"    attempts (when success): avg={mean(succ_attempts):.2f}  "
                     f"median={median(succ_attempts):.0f}")
    if words_succ:
        lines.append(f"    words: avg={mean(words_succ):.0f}  range={min(words_succ)}-{max(words_succ)}")
    if syl_succ:
        lines.append(f"    syllables: avg={mean(syl_succ):.0f}  range={min(syl_succ)}-{max(syl_succ)}")
    if err_types:
        et = "  ".join(f"{k}:{v}" for k, v in err_types.most_common())
        lines.append(f"    error types: {et}")
    return "\n".join(lines) + "\n"


def main() -> int:
    rows = _read_all()
    print("=" * 78)
    print(f"LM STUDIO BENCHMARK SUMMARY   ({len(rows)} generations on disk)")
    print("=" * 78)

    # Overall
    print()
    print("── OVERALL ──")
    print(_stat_block(rows, "all generations"))

    # Per-model
    print("── PER MODEL ──")
    by_model: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_model[r.get("model", "?")].append(r)
    for m in sorted(by_model.keys()):
        print(_stat_block(by_model[m], m))

    # Per-niche
    print("── PER NICHE ──")
    by_niche: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_niche[r.get("niche", "?")].append(r)
    for n in sorted(by_niche.keys()):
        print(_stat_block(by_niche[n], n))

    # Per-(model, niche) cross-table — success rate
    print("── MODEL × NICHE  (success / total, avg attempts) ──")
    cross: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        cross[(r.get("model", "?"), r.get("niche", "?"))].append(r)
    models = sorted({r.get("model", "?") for r in rows})
    niches = sorted({r.get("niche", "?") for r in rows})
    if models and niches:
        # Header
        header = "  niche".ljust(16) + "".join(f"{m[:14]:>15}" for m in models)
        print(header)
        for n in niches:
            line = f"  {n}".ljust(16)
            for m in models:
                cell = cross.get((m, n), [])
                if not cell:
                    line += f"{'—':>15}"
                else:
                    s = sum(1 for c in cell if c.get("success"))
                    avg_a = mean([c.get("attempts", 0) for c in cell if c.get("attempts")]) if any(c.get("attempts") for c in cell) else 0
                    line += f"{s}/{len(cell)} a{avg_a:.1f}".rjust(15)
            print(line)

    # Progress hint
    prog = BENCH / "_progress.json"
    if prog.exists():
        try:
            p = json.loads(prog.read_text(encoding="utf-8"))
            print()
            print("── LIVE PROGRESS ──")
            print(f"  {p.get('done')}/{p.get('total')}  elapsed={p.get('elapsed_min')} min  "
                  f"current: {p.get('current_model')} / {p.get('current_niche')}")
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
