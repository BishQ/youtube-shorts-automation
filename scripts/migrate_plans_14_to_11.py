"""Migrate plan.json from 14 clauses → 11 (schema CLAUSE_COUNT).

Default: all jobs created AFTER mike-tyson-33d52a25 (by plan.json mtime), skipping Tyson.

Usage:
  python scripts/migrate_plans_14_to_11.py --dry-run
  python scripts/migrate_plans_14_to_11.py
  python scripts/migrate_plans_14_to_11.py --after-job mike-tyson-33d52a25
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from shorts_pipeline.config.settings import Settings  # noqa: E402
from shorts_pipeline.jobs.models import ArtifactType, PipelineStage  # noqa: E402
from shorts_pipeline.jobs.store import JobStore  # noqa: E402
from shorts_pipeline.planner.schema import CLAUSE_COUNT, NarrationPlan  # noqa: E402

# Merge these middle-body clauses into the previous one (keeps word count / arc).
_MERGE_INTO_PREV = frozenset({4, 8, 11})


def _reduce_clauses(clauses: list[dict]) -> list[dict]:
    if len(clauses) == CLAUSE_COUNT:
        return [dict(c) for c in clauses]
    if len(clauses) != 14:
        raise ValueError(f"expected 14 or {CLAUSE_COUNT} clauses, got {len(clauses)}")
    out: list[dict] = []
    for i, clause in enumerate(clauses):
        c = dict(clause)
        if i in _MERGE_INTO_PREV:
            if not out:
                raise ValueError(f"cannot merge clause {i} — no previous clause")
            prev = out[-1]
            prev_text = (prev.get("text") or "").strip()
            extra = (c.get("text") or "").strip()
            if extra:
                prev["text"] = f"{prev_text} {extra}".strip() if prev_text else extra
            continue
        out.append(c)
    if len(out) != CLAUSE_COUNT:
        raise ValueError(f"merge produced {len(out)} clauses, expected {CLAUSE_COUNT}")
    return out


def _normalize_lever(data: dict) -> None:
    lever = data.get("decision_lever")
    if not isinstance(lever, dict):
        return
    lt = str(lever.get("lever_type", "")).strip().lower()
    if lt not in ("law", "geography", "politics"):
        lever["lever_type"] = "politics"


def migrate_plan_dict(data: dict) -> dict:
    clauses = data.get("clauses")
    if not isinstance(clauses, list):
        raise ValueError("missing clauses array")
    new_clauses = _reduce_clauses(clauses)
    out = dict(data)
    _normalize_lever(out)
    out["clauses"] = new_clauses
    # Preserve original narration length; clause texts now include merged lines.
    merged_script = " ".join(
        (c.get("text") or "").strip() for c in new_clauses if isinstance(c, dict)
    ).strip()
    # full_script must match clause texts for TTS + Whisper align (see rebuild_migrated_plans.py).
    out["full_script"] = merged_script
    niche = (out.get("niche") or "documentary").strip() or "documentary"
    out["niche"] = niche
    validated = NarrationPlan.model_validate(
        out,
        context={"allow_figure_name": True, "niche": niche},
    )
    return validated.model_dump(mode="json")


def jobs_after_anchor(jobs_dir: Path, anchor_job: str) -> list[Path]:
    anchor_plan = jobs_dir / anchor_job / "plan.json"
    if not anchor_plan.is_file():
        raise FileNotFoundError(f"anchor plan missing: {anchor_plan}")
    anchor_mt = anchor_plan.stat().st_mtime
    out: list[Path] = []
    for plan in jobs_dir.glob("*/plan.json"):
        if plan.parent.name == anchor_job:
            continue
        if plan.stat().st_mtime > anchor_mt:
            out.append(plan)
    out.sort(key=lambda p: p.stat().st_mtime)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--after-job",
        default="mike-tyson-33d52a25",
        help="Migrate jobs with plan.json mtime strictly after this job folder",
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    settings = Settings()
    jobs_dir = settings.data_dir / "jobs"
    store = JobStore(settings.data_dir / "jobs.sqlite")

    try:
        plans = jobs_after_anchor(jobs_dir, args.after_job)
    except FileNotFoundError as e:
        print(e, file=sys.stderr)
        return 1

    print(f"Anchor: {args.after_job} (skip)")
    print(f"Plans to migrate: {len(plans)}")
    if not plans:
        return 0

    ok = skip = fail = 0
    for plan_path in plans:
        job_id = plan_path.parent.name
        raw = json.loads(plan_path.read_text(encoding="utf-8"))
        fig = raw.get("historical_figure", "?")
        n = len(raw.get("clauses") or [])
        if n == CLAUSE_COUNT:
            skip += 1
            if args.dry_run:
                print(f"  skip (already {CLAUSE_COUNT}): {job_id} — {fig}")
            continue
        if n != 14:
            fail += 1
            print(f"  FAIL {job_id}: {n} clauses (not 14)", file=sys.stderr)
            continue
        try:
            migrated = migrate_plan_dict(raw)
        except Exception as exc:
            fail += 1
            print(f"  FAIL {job_id} — {fig}: {exc}", file=sys.stderr)
            continue
        if args.dry_run:
            print(
                f"  would migrate: {job_id} - {fig} "
                f"({n}->{len(migrated['clauses'])} clauses, "
                f"{len(raw.get('full_script', '').split())} words kept)",
                flush=True,
            )
            ok += 1
            continue
        plan_path.write_text(
            json.dumps(migrated, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if store.get_job(job_id) is not None:
            store.add_artifact(
                job_id,
                PipelineStage.plan,
                ArtifactType.plan_json,
                plan_path,
                meta={"migrated_from_14": True, "clause_count": CLAUSE_COUNT},
            )
        ok += 1
        print(f"  OK {job_id} - {fig}", flush=True)

    print(f"\nDone — migrated={ok} skipped={skip} failed={fail}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
