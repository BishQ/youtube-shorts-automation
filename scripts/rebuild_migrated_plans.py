"""Rebuild 11-clause plans from full_script so narration + visuals stay aligned.

The first migrate_plans_14_to_11 pass merged clause *text* into the previous
beat but kept the old 14-sentence full_script — TTS/align then disagreed with
images. This script:

  1. Splits full_script into sentences.
  2. Groups them into 11 beats (same grouping as 14→11: merge 4,8,11 for n=14).
  3. Sets each clause's text from its sentence group.
  4. Keeps existing image_prompt / motion / beat for that slot.
  5. Leaves full_script as the full narration (all sentences).

Usage:
  python scripts/rebuild_migrated_plans.py --dry-run
  python scripts/rebuild_migrated_plans.py
  python scripts/rebuild_migrated_plans.py --after-job mike-tyson-33d52a25
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from shorts_pipeline.config.settings import Settings  # noqa: E402
from shorts_pipeline.jobs.models import ArtifactType, PipelineStage  # noqa: E402
from shorts_pipeline.jobs.store import JobStore  # noqa: E402
from shorts_pipeline.planner.schema import CLAUSE_COUNT, NarrationPlan  # noqa: E402

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _split_sentences(full_script: str) -> list[str]:
    text = (full_script or "").strip()
    if not text:
        return []
    parts = [s.strip() for s in _SENT_SPLIT.split(text) if s.strip()]
    return parts


def _merge_indices(n: int, target: int = CLAUSE_COUNT) -> frozenset[int]:
    """Pick (n - target) sentence indices to fold into the previous group."""
    need = n - target
    if need <= 0:
        return frozenset()
    if need == 3 and n == 14:
        return frozenset({4, 8, 11})
    body = list(range(2, max(3, n - 1)))
    if len(body) < need:
        return frozenset(body)
    step = len(body) / (need + 1)
    picks: list[int] = []
    for k in range(1, need + 1):
        picks.append(body[min(len(body) - 1, int(k * step))])
    return frozenset(picks)


def _sentence_groups(n: int) -> list[tuple[int, ...]]:
    merge = _merge_indices(n)
    groups: list[tuple[int, ...]] = []
    for i in range(n):
        if i in merge:
            if not groups:
                groups.append((i,))
            else:
                groups[-1] = groups[-1] + (i,)
        else:
            groups.append((i,))
    if len(groups) != CLAUSE_COUNT:
        raise ValueError(f"sentence grouping produced {len(groups)} groups for n={n}")
    return groups


def rebuild_plan_dict(data: dict) -> dict:
    clauses = data.get("clauses")
    if not isinstance(clauses, list) or len(clauses) != CLAUSE_COUNT:
        raise ValueError(f"expected {CLAUSE_COUNT} clauses, got {len(clauses or [])}")

    sentences = _split_sentences(data.get("full_script") or "")
    if len(sentences) < CLAUSE_COUNT:
        raise ValueError(f"full_script has only {len(sentences)} sentences")

    groups = _sentence_groups(len(sentences))
    new_clauses: list[dict] = []
    for gi, (clause, idxs) in enumerate(zip(clauses, groups)):
        c = dict(clause)
        c["text"] = " ".join(sentences[i] for i in idxs).strip()
        new_clauses.append(c)

    out = dict(data)
    out["clauses"] = new_clauses
    out["full_script"] = " ".join(sentences).strip()
    niche = (out.get("niche") or "documentary").strip() or "documentary"
    out["niche"] = niche

    lever = out.get("decision_lever")
    if isinstance(lever, dict):
        lt = str(lever.get("lever_type", "")).strip().lower()
        if lt not in ("law", "geography", "politics"):
            lever["lever_type"] = "politics"

    return NarrationPlan.model_validate(
        out,
        context={"allow_figure_name": True, "niche": niche},
    ).model_dump(mode="json")


def jobs_after_anchor(jobs_dir: Path, anchor_job: str) -> list[Path]:
    anchor_plan = jobs_dir / anchor_job / "plan.json"
    if not anchor_plan.is_file():
        raise FileNotFoundError(f"anchor plan missing: {anchor_plan}")
    anchor_mt = anchor_plan.stat().st_mtime
    out = [
        p
        for p in jobs_dir.glob("*/plan.json")
        if p.parent.name != anchor_job and p.stat().st_mtime > anchor_mt
    ]
    out.sort(key=lambda p: p.stat().st_mtime)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--after-job", default="mike-tyson-33d52a25")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    settings = Settings()
    store = JobStore(settings.data_dir / "jobs.sqlite")
    plans = jobs_after_anchor(settings.data_dir / "jobs", args.after_job)

    print(f"Rebuild {len(plans)} plans (after {args.after_job})", flush=True)
    ok = fail = 0
    for plan_path in plans:
        job_id = plan_path.parent.name
        try:
            raw = json.loads(plan_path.read_text(encoding="utf-8"))
            rebuilt = rebuild_plan_dict(raw)
        except Exception as exc:
            fail += 1
            print(f"  FAIL {job_id}: {exc}", file=sys.stderr, flush=True)
            continue

        if args.dry_run:
            sents = len(_split_sentences(rebuilt["full_script"]))
            print(
                f"  OK {job_id} - {rebuilt.get('historical_figure', '?')[:30]} "
                f"({sents} sents -> {CLAUSE_COUNT} beats)",
                flush=True,
            )
            ok += 1
            continue

        plan_path.write_text(
            json.dumps(rebuilt, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if store.get_job(job_id) is not None:
            store.add_artifact(
                job_id,
                PipelineStage.plan,
                ArtifactType.plan_json,
                plan_path,
                meta={"rebuilt_from_full_script": True, "clause_count": CLAUSE_COUNT},
            )
        ok += 1
        print(f"  OK {job_id}", flush=True)

    print(f"\nDone — ok={ok} fail={fail}", flush=True)
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
