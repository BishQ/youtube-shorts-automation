"""One clause: plan.json motion_prompt + images/clause_NNN.png → videos/clause_NNN.mp4.

Quick Wan I2V smoke test without running the full pipeline.

Usage (repo root):
  python scripts/test_one_i2v_from_plan.py marco-polo-a3daf13b
  python scripts/test_one_i2v_from_plan.py marco-polo-a3daf13b --clause 0 --duration 3.5

Requires ComfyUI :8188 (--lowvram), wan22_i2v_gguf_q4_local workflow + Wan GGUF models.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

_env = ROOT / ".env"
if _env.is_file():
    for line in _env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key.startswith("SHORTS_") and key not in os.environ:
            os.environ[key] = val
        elif key and key not in os.environ:
            os.environ[key] = val

os.environ.setdefault("SHORTS_I2V_ENABLED", "true")
os.environ.setdefault("SHORTS_I2V_WORKFLOW_NAME", "wan22_i2v_gguf_q4_local")


def _clause_duration_s(job_dir: Path, clause_idx: int, fallback: float) -> float:
    timings_path = job_dir / "clause_timings.json"
    if not timings_path.is_file():
        return fallback
    data = json.loads(timings_path.read_text(encoding="utf-8"))
    ranges = data if isinstance(data, list) else (data.get("ranges") or data.get("clause_ranges"))
    if not isinstance(ranges, list) or clause_idx >= len(ranges):
        return fallback
    r = ranges[clause_idx]
    if isinstance(r, dict):
        start = float(r.get("start_s", r.get("start", 0)))
        end = float(r.get("end_s", r.get("end", start + fallback)))
        return max(0.5, end - start)
    if isinstance(r, (list, tuple)) and len(r) >= 2:
        return max(0.5, float(r[1]) - float(r[0]))
    return fallback


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("job_id", help="Job folder under data/jobs/")
    ap.add_argument("--clause", type=int, default=0, help="Clause index (default 0)")
    ap.add_argument(
        "--duration",
        type=float,
        default=None,
        help="Clip length in seconds (default: from clause_timings or 3.5)",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output MP4 path (default: videos/clause_NNN.mp4)",
    )
    args = ap.parse_args()

    from shorts_pipeline.config.settings import Settings
    from shorts_pipeline.config.ui_store import effective_settings
    from shorts_pipeline.planner.schema import Beat, Clause
    from shorts_pipeline.video_worker.motion_resolver import resolve_motion_prompt
    from shorts_pipeline.video_worker.wan_i2v import WanI2VClient, load_i2v_bundle

    settings = effective_settings(Settings())
    settings.i2v_enabled = True
    wf_name = os.environ.get("SHORTS_I2V_WORKFLOW_NAME", settings.i2v_workflow_name)
    settings.i2v_workflow_name = wf_name

    job_dir = settings.data_dir / "jobs" / args.job_id
    plan_path = job_dir / "plan.json"
    if not job_dir.is_dir():
        print(f"Job not found: {job_dir}", file=sys.stderr)
        return 1
    if not plan_path.is_file():
        print(f"Missing {plan_path}", file=sys.stderr)
        return 1

    data = json.loads(plan_path.read_text(encoding="utf-8"))
    clauses_raw = data.get("clauses") or []
    if args.clause < 0 or args.clause >= len(clauses_raw):
        print(f"Clause index {args.clause} out of range (0..{len(clauses_raw) - 1})", file=sys.stderr)
        return 1

    c0 = clauses_raw[args.clause]
    if not isinstance(c0, dict):
        print("Invalid clause entry in plan.json", file=sys.stderr)
        return 1
    beat_raw = c0.get("beat") or {}
    clause = Clause.model_construct(
        **{**c0, "beat": Beat.model_validate(beat_raw) if isinstance(beat_raw, dict) else beat_raw}
    )
    figure = str(data.get("historical_figure") or args.job_id)
    niche = data.get("niche")
    img_path = job_dir / "images" / f"clause_{args.clause:03d}.png"
    if not img_path.is_file():
        print(f"Missing image: {img_path}", file=sys.stderr)
        return 1

    motion = resolve_motion_prompt(clause, niche=niche)
    duration_s = args.duration
    if duration_s is None:
        duration_s = _clause_duration_s(job_dir, args.clause, fallback=3.5)
    duration_s = min(float(settings.i2v_max_clip_duration_s), max(0.5, duration_s))

    out_path = args.out or (job_dir / "videos" / f"clause_{args.clause:03d}.mp4")
    out_path = out_path.resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    bundle_path = settings.workflows_dir / f"{wf_name}.json"
    bundle = load_i2v_bundle(bundle_path)

    print(f"Job:       {args.job_id}")
    print(f"Figure:    {figure}")
    print(f"Image:     {img_path.name}")
    print(f"Motion:    {motion}")
    print(f"Duration:  {duration_s:.2f}s")
    print(f"Workflow:  {wf_name}")
    print(f"Output:    {out_path}")
    print(f"Comfy:     {settings.comfy_base_url}")
    print("\n[i2v] generating (first run loads models — can take 10–20 min on 8GB)...\n")

    WanI2VClient(settings).generate_clip(
        bundle,
        image_path=img_path,
        motion_prompt=motion,
        duration_s=duration_s,
        out_path=out_path,
    )

    kb = out_path.stat().st_size // 1024
    print(f"\nDone: {out_path} ({kb} KB)")
    print(f"Open: explorer \"{out_path.parent}\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
