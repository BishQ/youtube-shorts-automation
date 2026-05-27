import json
import wave
from pathlib import Path

from shorts_pipeline.config.settings import Settings

p = Path("data/jobs/leonidas-i-b42ae1b9")
plan = json.loads((p / "plan.json").read_text(encoding="utf-8"))
script = plan["full_script"]
words = len(script.split())
clauses = " ".join(c["text"] for c in plan["clauses"])
clause_words = len(clauses.split())

with wave.open(str(p / "narration.wav"), "rb") as w:
    dur = w.getnframes() / float(w.getframerate())

s = Settings()
print("full_script_words", words)
print("clause_text_words", clause_words)
print("narration_seconds", round(dur, 2))
print("kokoro_speed", s.kokoro_speed)
print("target_min_words_documentary", 180)
print("expected_narration_sec", "54-57")
