"""Arch-bench report — winning architecture per model, per niche, overall."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "scripts_out" / "arch_bench"


def main() -> int:
    rows = []
    for jf in OUT.glob("*/*/*.json"):
        try:
            d = json.loads(jf.read_text(encoding="utf-8"))
            rows.append(d.get("_meta", {}))
        except Exception:
            continue
    if not rows:
        print("No data in", OUT); return 1

    print("=" * 78)
    print(f"ARCH BENCH REPORT — {len(rows)} generations")
    print("=" * 78)

    # Overall per-arch
    by_arch = defaultdict(list)
    for r in rows: by_arch[r.get("arch", "?")].append(r)
    print("\n── PER ARCHITECTURE (across all models + niches) ──")
    print(f"{'arch':14s} {'gens':>5s} {'succ':>5s} {'%':>6s} {'in_band%':>9s} {'avg_attempts':>13s} {'avg_t':>7s}")
    for a in sorted(by_arch.keys()):
        rs = by_arch[a]
        n = len(rs)
        succ = sum(1 for r in rs if r.get("success"))
        in_band = sum(1 for r in rs if r.get("in_band"))
        avg_at = mean(r.get("attempts_used", 0) for r in rs)
        avg_t = mean(r.get("total_time_s", 0) for r in rs if r.get("total_time_s"))
        print(f"{a:14s} {n:>5d} {succ:>5d} {100*succ/n:>5.0f}% {100*in_band/n:>8.0f}% {avg_at:>12.2f}  {avg_t:>6.0f}s")

    # Per model
    by_model = defaultdict(list)
    for r in rows: by_model[r.get("model", "?")].append(r)
    print("\n── PER MODEL ──")
    for m in sorted(by_model.keys()):
        rs = by_model[m]
        n = len(rs); succ = sum(1 for r in rs if r.get("success"))
        print(f"  {100*succ/n:>4.0f}%  ({succ}/{n})  {m}")

    # Model × Arch grid
    print("\n── MODEL × ARCH (success / total) ──")
    models = sorted(by_model.keys())
    archs = sorted(by_arch.keys())
    line = "  model".ljust(46) + "".join(f"{a[:13]:>14s}" for a in archs)
    print(line)
    for m in models:
        cells = []
        for a in archs:
            rs = [r for r in rows if r.get("model") == m and r.get("arch") == a]
            if not rs: cells.append(f"{'—':>14s}")
            else:
                ok = sum(1 for r in rs if r.get("success"))
                cells.append(f"{ok}/{len(rs)}".rjust(14))
        m_short = m if len(m) <= 44 else (m[:41] + "...")
        print(f"  {m_short}".ljust(46) + "".join(cells))

    # Failure tag totals
    print("\n── FAILURE TAGS (across all) ──")
    tags = Counter()
    for r in rows:
        if r.get("success"): continue
        for t in (r.get("error_tags") or "").split(";"):
            t = t.strip()
            if t: tags[t] += 1
    for t, c in tags.most_common(15):
        print(f"  {c:>4d}  {t}")

    # Best arch per niche (across all models)
    print("\n── BEST ARCH PER NICHE ──")
    niches = sorted({r.get("niche", "?") for r in rows})
    for n in niches:
        best_a = None; best_score = -1
        for a in archs:
            cell = [r for r in rows if r.get("niche") == n and r.get("arch") == a]
            if not cell: continue
            s = sum(1 for c in cell if c.get("success")) + 0.5 * sum(1 for c in cell if c.get("in_band"))
            if s > best_score:
                best_score = s; best_a = a
        print(f"  {n:14s} → {best_a or 'none'}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
