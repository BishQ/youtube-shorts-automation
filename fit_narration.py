"""Per-job narration speed fitter + hard 59.5 s cap.

Measure narration.wav duration; if it overshoots the Shorts cap, recompute
``kokoro_speed`` from the overshoot ratio and re-render. As a last resort
(when the speed-clamped render is still over the cap), the audio is HARD-
CUT at 59.5 s with a 300 ms fadeout — preferable to a Short that runs >60 s
and gets demoted by YouTube.

Hierarchy of attempts (best → worst quality):
  1. ≤59 s on the first render — keep as-is.
  2. Speed bump to a computed value ≤``max_speed`` (default 1.22). Kokoro is
     verified stable below ~1.30; 1.35+ drops content. 1.22 keeps audible
     prosody intact.
  3. Render at ``max_speed``, then hard-cut at ``target + 0.5 s`` (default
     59.5 s) with a 300 ms fadeout. Logged as ``hard_cut`` for review.

Backups: the original render is preserved as ``narration_original.wav``.

Usage:
    python fit_narration.py data/jobs/batch_001/001_john-napier
    python fit_narration.py data/jobs/batch_001 --target 58 --max-speed 1.22
    python fit_narration.py data/jobs/batch_001 --dry-run
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from shorts_pipeline.config.settings import Settings  # noqa: E402
from shorts_pipeline.tts_worker.kokoro import KokoroTTSClient  # noqa: E402


def _probe_duration(wav: Path) -> float:
    out = subprocess.check_output(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(wav),
        ],
        text=True,
    )
    return float(out.strip())


def _hard_cut(src: Path, dst: Path, end_s: float, fade_ms: int = 300) -> None:
    """Trim ``src`` to ``end_s`` seconds and apply a fadeout near the end."""
    fade_start = max(0.0, end_s - fade_ms / 1000.0)
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-i", str(src),
        "-t", f"{end_s:.3f}",
        "-af", f"afade=t=out:st={fade_start:.3f}:d={fade_ms/1000:.3f}",
        "-ar", "24000",
        str(dst),
    ]
    subprocess.check_call(cmd)


def _iter_jobs(root: Path):
    if (root / "plan.json").exists() and (root / "narration.wav").exists():
        yield root
        return
    for sub in sorted(root.iterdir()):
        if not sub.is_dir():
            continue
        if (sub / "plan.json").exists() and (sub / "narration.wav").exists():
            yield sub


def fit_one(
    job_dir: Path,
    *,
    target_min: float,
    target_max: float,
    hard_cap: float,
    max_speed: float,
    min_speed: float,
    dry_run: bool,
) -> None:
    wav = job_dir / "narration.wav"
    backup = job_dir / "narration_original.wav"

    cur_dur = _probe_duration(wav)

    if target_min <= cur_dur <= target_max:
        print(f"  {job_dir.name}: {cur_dur:.2f}s -- inside [{target_min}, {target_max}], skip")
        return

    # Initial speed guess from a linear-ish prior. Kokoro's speed→duration
    # mapping is NON-linear (speed=0.85 makes audio ~1.12× longer, not 1/0.85=1.18×),
    # so the first attempt usually misses. We iterate up to 4 passes, nudging
    # speed by ±0.03 per pass until we land in band or hit the speed bounds.
    target_mid = (target_min + target_max) / 2
    if cur_dur < target_min:
        # need slower (lower speed)
        new_speed = max(min_speed, round(cur_dur / target_max, 2))
    else:
        new_speed = min(max_speed, round(cur_dur / target_mid, 2))

    print(f"  {job_dir.name}: {cur_dur:.2f}s -> iterate from speed={new_speed}")
    if dry_run:
        return

    plan = json.loads((job_dir / "plan.json").read_text(encoding="utf-8"))
    text = plan["full_script"]

    if not backup.exists():
        shutil.copy2(wav, backup)

    s = Settings()
    new_dur = cur_dur
    for it in range(5):
        s.kokoro_speed = new_speed
        wav.unlink(missing_ok=True)
        KokoroTTSClient(s).synthesize_wav(text, wav)
        new_dur = _probe_duration(wav)
        print(f"    iter {it+1}: speed={new_speed:.2f} -> {new_dur:.2f}s")

        if target_min <= new_dur <= target_max:
            print(f"    -> {new_dur:.2f}s  [OK]")
            return

        if new_dur < target_min:
            # need slower → decrease speed
            step = 0.03 if (target_min - new_dur) < 2 else 0.05
            next_speed = round(new_speed - step, 2)
        else:
            step = 0.03 if (new_dur - target_max) < 2 else 0.05
            next_speed = round(new_speed + step, 2)

        if next_speed <= min_speed:
            next_speed = min_speed
        if next_speed >= max_speed:
            next_speed = max_speed
        if next_speed == new_speed:
            break  # at boundary, can't improve
        new_speed = next_speed

    if new_dur <= hard_cap:
        flag = "UNDER-BAND-BUT-IN-CAP" if new_dur < target_min else "OVER-BAND-BUT-IN-CAP"
        print(f"    -> {new_dur:.2f}s  [{flag} after {it+1} iters]")
        return

    # Still over the hard cap even at max_speed — apply a hard cut + fadeout.
    cut_dst = job_dir / "narration_cut.wav"
    _hard_cut(wav, cut_dst, hard_cap, fade_ms=300)
    cut_dur = _probe_duration(cut_dst)
    wav.unlink()
    cut_dst.rename(wav)
    print(f"    -> {cut_dur:.2f}s  [HARD-CUT @ {hard_cap}s + 300ms fadeout]")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", help="Job folder or batch folder containing job subfolders")
    # Acceptance band: 55-59.5 s. Kokoro has ~4-5 s stochasticity between
    # renders of the same script, so re-rendering inside a narrower band rarely
    # converges. 55-59.5 gives breathing room while keeping the body comfortably
    # close to the upper edge of the Shorts window (60 s).
    ap.add_argument("--target-min", type=float, default=55.0,
                    help="Lower edge of the acceptable duration band (s).")
    ap.add_argument("--target-max", type=float, default=59.5,
                    help="Upper edge of the acceptable duration band (s).")
    ap.add_argument("--hard-cap", type=float, default=59.5,
                    help="Absolute hard cap (s) before audio is cut.")
    ap.add_argument("--max-speed", type=float, default=1.22,
                    help="Upper bound on kokoro_speed. >1.30 drops content; 1.22 keeps prosody.")
    ap.add_argument("--min-speed", type=float, default=0.85,
                    help="Lower bound on kokoro_speed. <0.85 muddies the voice.")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    root = Path(args.path)
    if not root.exists():
        sys.exit(f"path not found: {root}")
    if args.target_min > args.target_max:
        sys.exit("--target-min must be <= --target-max")
    if args.hard_cap < args.target_max:
        sys.exit("--hard-cap must be >= --target-max")

    jobs = list(_iter_jobs(root))
    if not jobs:
        sys.exit(f"no job folders with plan.json + narration.wav under {root}")

    print(f"Fitting {len(jobs)} job(s); accept [{args.target_min}, {args.target_max}]s, "
          f"hard cap={args.hard_cap}s, clamp speed<={args.max_speed}")
    for j in jobs:
        fit_one(
            j,
            target_min=args.target_min,
            target_max=args.target_max,
            hard_cap=args.hard_cap,
            max_speed=args.max_speed,
            min_speed=args.min_speed,
            dry_run=args.dry_run,
        )


if __name__ == "__main__":
    main()
