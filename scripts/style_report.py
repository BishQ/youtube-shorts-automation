"""Style-bench report — success % per style, top failure tags."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "scripts_out" / "style_bench"


def main() -> int:
    rows = []
    for jf in OUT.glob("*/*/*.json"):
        try:
            d = json.loads(jf.read_text(encoding="utf-8"))
            rows.append(d.get("_meta", {}))
        except Exception:
            continue

    if not rows:
        print("No data found in", OUT)
        return 1

    # ── Per-style summary ────────────────────────────────────────────────────
    by_style: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_style[r.get("style", "?")].append(r)

    print("=" * 78)
    print(f"STYLE BENCH REPORT — {len(rows)} total generations")
    print("=" * 78)

    print("\n── PER STYLE ──")
    print(f"{'style':12s} {'gens':>5s} {'succ':>5s} {'%':>6s} {'in_band%':>9s} {'avg_t':>7s} {'top failures'}")
    style_stats = {}
    for style in sorted(by_style.keys()):
        rs = by_style[style]
        n = len(rs)
        succ = sum(1 for r in rs if r.get("success"))
        in_band = sum(1 for r in rs if r.get("in_band"))
        avg_t = mean(r["time_s"] for r in rs if isinstance(r.get("time_s"), (int, float)))
        tags = Counter()
        for r in rs:
            if not r.get("success"):
                for t in (r.get("error_tags") or "").split(";"):
                    t = t.strip()
                    if t: tags[t] += 1
        top = " ".join(f"{k}:{v}" for k, v in tags.most_common(3))
        style_stats[style] = {"n": n, "succ": succ, "in_band": in_band, "avg_t": avg_t, "tags": tags}
        print(f"{style:12s} {n:>5d} {succ:>5d} {100*succ/n:>5.0f}% {100*in_band/n:>8.0f}% {avg_t:>6.0f}s  {top}")

    # ── Per-niche × style cross ──────────────────────────────────────────────
    print("\n── PER NICHE × STYLE (success / total) ──")
    niches = sorted({r.get("niche", "?") for r in rows})
    styles = sorted(by_style.keys())
    header = "  niche".ljust(16) + "".join(f"{s:>11s}" for s in styles)
    print(header)
    for n in niches:
        line = f"  {n}".ljust(16)
        for s in styles:
            cell = [r for r in by_style[s] if r.get("niche") == n]
            if not cell:
                line += f"{'—':>11}"
            else:
                ok = sum(1 for c in cell if c.get("success"))
                line += f"{ok}/{len(cell)}".rjust(11)
        print(line)

    # ── Failure tag totals across styles ─────────────────────────────────────
    print("\n── FAILURE TAGS (across all styles) ──")
    all_tags = Counter()
    for r in rows:
        if r.get("success"): continue
        for t in (r.get("error_tags") or "").split(";"):
            t = t.strip()
            if t: all_tags[t] += 1
    for t, c in all_tags.most_common(15):
        print(f"  {c:>4d}  {t}")

    # ── Which style wins for each niche ──────────────────────────────────────
    print("\n── BEST STYLE PER NICHE ──")
    for n in niches:
        best_style = None
        best_score = -1
        for s in styles:
            cell = [r for r in by_style[s] if r.get("niche") == n]
            if not cell: continue
            score = sum(1 for c in cell if c.get("success")) + (sum(1 for c in cell if c.get("in_band")) * 0.5)
            if score > best_score:
                best_score = score
                best_style = s
        print(f"  {n:14s} → {best_style or 'none'}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
