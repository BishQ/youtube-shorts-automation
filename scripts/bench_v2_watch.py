"""Tail bench_v2 JSON files and emit one line per new file with the failure reason."""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "scripts_out" / "bench_v2"

seen: set[str] = set()
# Seed with files already on disk so we only report NEW ones
for jf in BENCH.glob("*/*.json"):
    seen.add(str(jf))

while True:
    for jf in sorted(BENCH.glob("*/*.json"), key=lambda p: p.stat().st_mtime):
        if str(jf) in seen:
            continue
        seen.add(str(jf))
        try:
            d = json.loads(jf.read_text(encoding="utf-8"))
        except Exception:
            continue
        meta = d.get("_meta", {})
        title = meta.get("title", "")[:60]
        succ = meta.get("success")
        if succ:
            print(f"OK {meta.get('niche')}#{meta.get('idx')} {meta.get('time_s')}s words={meta.get('words')} :: {title}", flush=True)
            continue
        err = meta.get("error", "")
        # Pull all 'Value error,' messages
        msgs = re.findall(r"Value error, ([^\n]+)", err)
        reasons: list[str] = []
        for m in msgs:
            m = m.strip()
            if "is only" in m and "minimum is" in m:
                # Extract numbers
                mm = re.search(r"only (\d+) words .*minimum is (\d+)", m)
                if mm:
                    reasons.append(f"SHORT {mm.group(1)}<{mm.group(2)}w")
                else:
                    reasons.append("SHORT")
            elif "word limit" in m.lower():
                mm = re.search(r"is (\d+) words .*limit of (\d+)", m)
                reasons.append(f"LONG {mm.group(1)}>{mm.group(2)}w" if mm else "LONG")
            elif "syllable" in m.lower():
                mm = re.search(r"contains (\d+) syllables.*cap of (\d+)", m, re.IGNORECASE)
                reasons.append(f"SYL {mm.group(1)}>{mm.group(2)}" if mm else "SYL over")
            elif "banned content" in m:
                ban = re.search(r"'([^']+)'", m)
                reasons.append(f"BANNED:{ban.group(1)[:20] if ban else '?'}")
            elif "lighting" in m.lower():
                reasons.append("NO-LIGHTING")
            elif "shot type" in m.lower():
                reasons.append("NO-SHOT")
            elif "question" in m.lower():
                reasons.append("?-COUNT")
            elif "motion" in m.lower():
                reasons.append("MOTION")
            elif "date" in m.lower():
                reasons.append("DATE")
            elif "clause" in m.lower():
                reasons.append("CLAUSE")
            elif "figure" in m.lower():
                reasons.append("FIGURE-NAME")
            else:
                reasons.append(m[:40].replace("\n", " "))
        if not reasons:
            # Connection / HTTP / other path
            if "HTTP" in err:
                mm = re.search(r"HTTP (\d+)", err)
                reasons.append(f"HTTP{mm.group(1) if mm else '?'}")
            elif "connection" in err.lower():
                reasons.append("CONN")
            elif "timeout" in err.lower():
                reasons.append("TIMEOUT")
            else:
                reasons.append("UNKNOWN")
        print(f"FAIL {meta.get('niche')}#{meta.get('idx')} {meta.get('time_s')}s [{', '.join(reasons)}] :: {title}", flush=True)
    time.sleep(2)
