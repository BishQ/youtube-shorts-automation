"""
Regenerate the Kareem Abdul-Jabbar job with 14 clauses (~60 seconds).
Stages: plan → TTS → images → align → render
All output goes to data/jobs/kareem-abdul-jabar-c2d974ea/ (overwrites existing files).
"""
import sys
import json
import logging
import shutil

sys.path.insert(0, "src")

from pathlib import Path
from shorts_pipeline.config.settings import Settings
from shorts_pipeline.aligner.ass import build_ass_karaoke
from shorts_pipeline.aligner.clause_times import clause_time_ranges_from_words
from shorts_pipeline.aligner.faster_whisper_backend import FasterWhisperAligner
from shorts_pipeline.editor import build_edit_plan_from_ranges
from shorts_pipeline.image_worker.hybrid_image_generator import HybridImageGenerator
from shorts_pipeline.planner.router import build_planner_client
from shorts_pipeline.renderer.ffmpeg import RenderRequest, render_short
from shorts_pipeline.tts_worker.kokoro import KokoroTTSClient

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)

JOB_DIR = Path("data/jobs/kareem-abdul-jabar-c2d974ea")
BGM_PATH = Path("assets/bgm.wav")
FIGURE_NAME = "Kareem Abdul-Jabbar"

settings = Settings()

# ── Stage 1: Plan ─────────────────────────────────────────────────────────────
log.info("=" * 60)
log.info("STAGE 1/5 — Generating plan (14 clauses, 155-162 words) ...")
log.info("=" * 60)

planner = build_planner_client(settings)
plan = planner.generate_plan(FIGURE_NAME, topic_type="historical_figure", language="english")

log.info(
    "Plan generated: %d clauses, %d words",
    len(plan.clauses),
    len(plan.full_script.split()),
)
log.info("Script preview: %s", plan.full_script[:120])

plan_path = JOB_DIR / "plan.json"
plan_path.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
log.info("Saved plan.json")

# ── Stage 2: TTS ──────────────────────────────────────────────────────────────
log.info("=" * 60)
log.info("STAGE 2/5 — Synthesising narration (%d words) ...", len(plan.full_script.split()))
log.info("=" * 60)

wav_path = JOB_DIR / "narration.wav"
KokoroTTSClient(settings).synthesize_wav(plan.full_script, wav_path)

import wave
with wave.open(str(wav_path)) as wf:
    duration = wf.getnframes() / wf.getframerate()
log.info("Narration done: %.2fs", duration)

# ── Stage 3: Images ───────────────────────────────────────────────────────────
log.info("=" * 60)
log.info("STAGE 3/5 — Generating %d images ...", len(plan.clauses))
log.info("=" * 60)

images_dir = JOB_DIR / "images"
# Clear old images so stale ones don't interfere
if images_dir.exists():
    shutil.rmtree(images_dir)
images_dir.mkdir(parents=True, exist_ok=True)

prompts = [c.image_prompt for c in plan.clauses]
total = len(prompts)

def _progress(i: int, prompt: str) -> None:
    log.info("  Image %d/%d done — %s", i + 1, total, prompt[:70])

image_paths = HybridImageGenerator(settings).generate_all(
    prompts, images_dir, progress_cb=_progress
)
log.info("All %d images generated", len(image_paths))

# ── Stage 4: Alignment ────────────────────────────────────────────────────────
log.info("=" * 60)
log.info("STAGE 4/5 — Aligning word timestamps ...")
log.info("=" * 60)

aligner = FasterWhisperAligner(settings)
words = aligner.align_words(wav_path, plan.full_script)
log.info("Alignment done: %d word spans, last=%.2fs", len(words), words[-1].end_s)

ranges = clause_time_ranges_from_words(plan, words)
log.info("Clause time ranges:")
for i, (s, e) in enumerate(ranges):
    log.info("  [%2d] %6.2fs - %6.2fs  (%.2fs)  %s", i, s, e, e - s, plan.clauses[i].text[:50])

total_duration = max(e for _, e in ranges)
log.info("Total video duration: %.2fs", total_duration)

timings_path = JOB_DIR / "clause_timings.json"
timings_path.write_text(json.dumps(ranges, indent=2), encoding="utf-8")

ass_path = JOB_DIR / "subtitles.ass"
build_ass_karaoke(plan, words, settings, ass_path, narration_end_s=duration)
log.info("Saved clause_timings.json + subtitles.ass")

# ── Stage 5: Render ───────────────────────────────────────────────────────────
log.info("=" * 60)
log.info("STAGE 5/5 — Rendering final video ...")
log.info("=" * 60)

image_paths_sorted = sorted(images_dir.glob("clause_*.png"))
if len(image_paths_sorted) != len(plan.clauses):
    raise RuntimeError(
        f"image count mismatch: {len(image_paths_sorted)} files vs {len(plan.clauses)} clauses"
    )

edit = build_edit_plan_from_ranges(
    plan,
    image_paths_sorted,
    ranges,
    total_duration,
    bgm_path=BGM_PATH,
    snap_to_bgm_beats=settings.snap_to_bgm_beats,
    run_face_detection=True,
)

out_mp4 = JOB_DIR / "final.mp4"
req = RenderRequest(
    edit=edit,
    narration_wav=wav_path,
    ass_path=ass_path,
    out_mp4=out_mp4,
    narration_duration_s=total_duration,
)

render_short(req, settings)

import subprocess
result = subprocess.run(
    ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(out_mp4)],
    capture_output=True, text=True,
)
info = json.loads(result.stdout)
actual_duration = float(info["format"]["duration"])

log.info("=" * 60)
log.info("DONE!  Output: %s", out_mp4)
log.info("Final video duration: %.2f seconds", actual_duration)
log.info("=" * 60)
