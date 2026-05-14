"""
Re-run ONLY align + render for kareem using the new subtitle design.
Reuses existing plan.json, narration.wav, and images.
"""
import sys
import json
import logging
import wave

sys.path.insert(0, "src")

from pathlib import Path
from shorts_pipeline.config.settings import Settings
from shorts_pipeline.aligner.ass import build_ass_karaoke
from shorts_pipeline.aligner.clause_times import clause_time_ranges_from_words
from shorts_pipeline.aligner.faster_whisper_backend import FasterWhisperAligner
from shorts_pipeline.editor import build_edit_plan_from_ranges
from shorts_pipeline.planner.schema import Beat, Clause, LutChoice, NarrationPlan
from shorts_pipeline.renderer.ffmpeg import RenderRequest, render_short

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)

JOB_DIR = Path("data/jobs/kareem-abdul-jabar-c2d974ea")
BGM_PATH = Path("extra tools/bgm.mp3")

raw = json.loads((JOB_DIR / "plan.json").read_text(encoding="utf-8"))
clauses = [Clause.model_construct(text=c["text"], image_prompt=c.get("image_prompt", ""),
           beat=Beat.model_validate(c.get("beat", {}))) for c in raw["clauses"]]
plan = NarrationPlan.model_construct(
    historical_figure=raw["historical_figure"],
    cold_open_object=raw["cold_open_object"],
    decision_lever=raw.get("decision_lever"),
    clauses=clauses,
    full_script=raw["full_script"],
    lut_choice=LutChoice(raw["lut_choice"]),
    end_plate_question=raw["end_plate_question"],
)

settings = Settings()

with wave.open(str(JOB_DIR / "narration.wav")) as wf:
    narration_end_s = wf.getnframes() / wf.getframerate()
log.info("Narration duration: %.2fs", narration_end_s)

log.info("Running alignment ...")
aligner = FasterWhisperAligner(settings)
words = aligner.align_words(JOB_DIR / "narration.wav", plan.full_script)
log.info("Alignment: %d spans, last=%.2fs", len(words), words[-1].end_s)

ranges = clause_time_ranges_from_words(plan, words)
for i, (s, e) in enumerate(ranges):
    log.info("  [%2d] %6.2fs-%-6.2fs  %s", i, s, e, plan.clauses[i].text[:50])

(JOB_DIR / "clause_timings.json").write_text(json.dumps(ranges, indent=2), encoding="utf-8")

ass_path = JOB_DIR / "subtitles.ass"
build_ass_karaoke(plan, words, settings, ass_path, narration_end_s=narration_end_s)
log.info("Subtitles written: %s", ass_path)

total_duration = max(e for _, e in ranges)
image_paths = sorted((JOB_DIR / "images").glob("clause_*.png"))
edit = build_edit_plan_from_ranges(plan, image_paths, ranges, total_duration,
                                   bgm_path=BGM_PATH, snap_to_bgm_beats=settings.snap_to_bgm_beats,
                                   run_face_detection=True)

out_mp4 = JOB_DIR / "final.mp4"
req = RenderRequest(edit=edit, narration_wav=JOB_DIR / "narration.wav",
                    ass_path=ass_path, out_mp4=out_mp4, narration_duration_s=total_duration)
log.info("Rendering ...")
render_short(req, settings)
log.info("Done → %s", out_mp4)
