"""Wires pipeline stages to SQLite artifacts (StageHandler implementation)."""

from __future__ import annotations

import hashlib
import json
import subprocess
import wave
from pathlib import Path
from typing import Any

from shorts_pipeline.aligner.ass import build_ass_karaoke
from shorts_pipeline.aligner.clause_times import clause_time_ranges_from_words
from shorts_pipeline.aligner.base import load_aligner
from shorts_pipeline.config.settings import Settings
from shorts_pipeline.editor import build_edit_plan_from_ranges
from shorts_pipeline.image_worker.comfy import ComfyClient, ComfyError, load_workflow_bundle
from shorts_pipeline.image_worker.grok_bookend_generator import GrokBookendImageGenerator
from shorts_pipeline.image_worker.hybrid_image_generator import HybridImageGenerator
from shorts_pipeline.image_worker.triple_hybrid_image_generator import TripleHybridImageGenerator
from shorts_pipeline.image_worker.smart_grok_image_generator import SmartGrokImageGenerator
from shorts_pipeline.jobs.exceptions import CooperativePauseError
from shorts_pipeline.jobs.image_order import (
    clause_index_from_video_path,
    sort_clause_mp4_artifacts,
    sort_clause_png_artifacts,
)
from shorts_pipeline.jobs.models import ArtifactType, JobStatus, PipelineStage
from shorts_pipeline.jobs.store import JobStore, verify_artifact_path
from shorts_pipeline.logging_setup import get_logger
from shorts_pipeline.planner.router import build_planner_client
from shorts_pipeline.planner.schema import NarrationPlan
from shorts_pipeline.runpod_adapter import make_i2v_client
from shorts_pipeline.video_worker.motion_resolver import resolve_motion_prompt
from shorts_pipeline.video_worker.wan_i2v import load_i2v_bundle
from shorts_pipeline.publisher import (
    PublishingPackage,
    build_fallback_package,
    build_publisher_client,
    render_package_text,
)
from shorts_pipeline.renderer.ffmpeg import RenderRequest, render_short

log = get_logger(__name__)


class PipelineOrchestrator:
    def __init__(self, settings: Settings, store: JobStore) -> None:
        self._settings = settings
        self._store = store

    def _job_dir(self, job_id: str) -> Path:
        return (self._settings.data_dir / "jobs" / job_id).resolve()

    def _pause_after_image_flag(self, job_id: str) -> Path:
        return self._job_dir(job_id) / "images" / ".pause_after_image"

    def _consume_pause_after_image(self, job_id: str) -> None:
        flag = self._pause_after_image_flag(job_id)
        if flag.is_file():
            flag.unlink(missing_ok=True)
            raise CooperativePauseError()

    def _image_artifact_valid_for_path(self, job_id: str, out_path: Path) -> bool:
        arts = self._store.get_artifacts_for_stage(job_id, PipelineStage.images, ArtifactType.image_png)
        resolved = out_path.resolve()
        for a in arts:
            if Path(a.path).resolve() == resolved:
                return verify_artifact_path(Path(a.path), a.sha256)
        return False

    def _try_register_existing_clause_png(
        self,
        job_id: str,
        out_path: Path,
        comfy: ComfyClient,
        bundle: Any,
    ) -> bool:
        """Register a valid on-disk clause PNG that has no artifact row yet (crash recovery)."""
        if self._image_artifact_valid_for_path(job_id, out_path):
            return True
        if not out_path.is_file():
            return False
        try:
            comfy.verify_png(out_path, min_w=bundle.min_width, min_h=bundle.min_height)
        except ComfyError:
            return False
        self._store.add_artifact(
            job_id,
            PipelineStage.images,
            ArtifactType.image_png,
            out_path,
            meta={"source": "recovered_from_disk"},
        )
        log.info("image_artifact_recovered", job_id=job_id, path=out_path.name)
        return True

    def _shared_outro_image_path(self) -> Path:
        return (self._settings.assets_dir / "outro.png").resolve()

    def _ensure_shared_outro_image(self) -> None:
        """Generate the Flux outro background image ONCE and reuse across all jobs.

        Stored at assets/outro.png. If it already exists as a non-empty file, skip.
        SOFT FAILURE: ComfyUI errors are logged as warnings; renderer falls back
        to the blurred last clip so no job is blocked by a missing outro image.
        """
        out_path = self._shared_outro_image_path()
        if out_path.is_file() and out_path.stat().st_size > 1000:
            log.info("shared_outro_image_exists", path=str(out_path))
            return

        try:
            wf_name = self._settings.comfy_workflow_name
            bundle_path = (self._settings.workflows_dir / f"{wf_name}.json").resolve()
            bundle = load_workflow_bundle(bundle_path)
            comfy = ComfyClient(self._settings)

            out_path.parent.mkdir(parents=True, exist_ok=True)
            prompt = self._settings.outro_image_prompt
            log.info("shared_outro_image_generating", prompt_preview=prompt[:80])
            comfy.generate_one(bundle, prompt, out_path)
            log.info("shared_outro_image_done", path=str(out_path))
        except (ComfyError, OSError) as exc:
            log.warning(
                "shared_outro_image_failed_falling_back",
                error=str(exc)[:300],
                error_type=type(exc).__name__,
            )

    # ── Stages ────────────────────────────────────────────────────────────────

    def run_plan(self, job_id: str) -> None:
        rec = self._store.get_job(job_id)
        if rec is None:
            raise ValueError("job not found")
        cfg = rec.config_snapshot
        log.info("plan_generating", job_id=job_id, figure=cfg.figure_name)
        client = build_planner_client(self._settings)
        plan = client.generate_plan(
            cfg.figure_name,
            topic_type=cfg.topic_type,
            language=cfg.language,
        )
        jd = self._job_dir(job_id)
        jd.mkdir(parents=True, exist_ok=True)
        plan_path = jd / "plan.json"
        plan_path.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
        self._store.add_artifact(
            job_id,
            PipelineStage.plan,
            ArtifactType.plan_json,
            plan_path,
            meta={"figure": cfg.figure_name},
        )
        log.info(
            "plan_done",
            job_id=job_id,
            figure=cfg.figure_name,
            clauses=len(plan.clauses),
            words=len(plan.full_script.split()),
            script_preview=plan.full_script[:120],
        )

    def run_images(self, job_id: str) -> None:
        rec = self._store.get_job(job_id)
        if rec is None:
            raise ValueError("job not found")
        plan = self._load_plan(job_id)
        prompts = [c.image_prompt for c in plan.clauses]
        out_dir = self._job_dir(job_id) / "images"
        total = len(prompts)
        backend = self._settings.image_backend.lower().strip()
        log.info("images_start", job_id=job_id, total=total, backend=backend)

        incremental_image_artifacts = False
        if backend == "hybrid":
            paths = HybridImageGenerator(self._settings).generate_all(
                prompts,
                out_dir,
                progress_cb=lambda i, prompt: log.info(
                    "image_done",
                    job_id=job_id,
                    index=i + 1,
                    total=total,
                    backend="hybrid",
                    prompt_preview=prompt[:80],
                ),
            )
        elif backend == "hybrid_grok_bookends":
            figure = (rec.config_snapshot.figure_name or plan.historical_figure or "").strip() or None
            paths = GrokBookendImageGenerator(self._settings).generate_all(
                prompts,
                out_dir,
                figure_display_name=figure,
                progress_cb=lambda i, prompt: log.info(
                    "image_done",
                    job_id=job_id,
                    index=i + 1,
                    total=total,
                    backend="hybrid_grok_bookends",
                    prompt_preview=prompt[:80],
                ),
            )
        elif backend == "hybrid_grok_flux":
            figure = (rec.config_snapshot.figure_name or plan.historical_figure or "").strip() or None
            paths = TripleHybridImageGenerator(self._settings).generate_all(
                prompts,
                out_dir,
                figure_display_name=figure,
                resume=True,
                progress_cb=lambda i, prompt: log.info(
                    "image_done",
                    job_id=job_id,
                    index=i + 1,
                    total=total,
                    backend="hybrid_grok_flux",
                    prompt_preview=prompt[:80],
                ),
            )
        elif backend == "smart_grok":
            figure = (rec.config_snapshot.figure_name or plan.historical_figure or "").strip() or None
            figure_flags = [c.figure_present for c in plan.clauses]
            paths = SmartGrokImageGenerator(self._settings).generate_all(
                prompts,
                figure_flags,
                out_dir,
                figure_display_name=figure,
                progress_cb=lambda i, prompt: log.info(
                    "image_done",
                    job_id=job_id,
                    index=i + 1,
                    total=total,
                    backend="smart_grok",
                    prompt_preview=prompt[:80],
                ),
            )
        else:
            # "comfy" (default): resume-friendly — one artifact per image, skip existing, optional pause.
            incremental_image_artifacts = True
            wf_name = rec.config_snapshot.comfy_workflow_name or self._settings.comfy_workflow_name
            bundle_path = (self._settings.workflows_dir / f"{wf_name}.json").resolve()
            bundle = load_workflow_bundle(bundle_path)
            comfy = ComfyClient(self._settings)
            out_dir.mkdir(parents=True, exist_ok=True)
            paths: list[Path] = []
            for i, ptxt in enumerate(prompts):
                log.info(
                    "image_generating",
                    index=i + 1,
                    total=len(prompts),
                    prompt_preview=ptxt[:80],
                )
                out_path = out_dir / f"clause_{i:03d}.png"
                if self._image_artifact_valid_for_path(job_id, out_path):
                    paths.append(out_path)
                    log.info(
                        "image_done",
                        job_id=job_id,
                        index=i + 1,
                        total=total,
                        backend="comfy",
                        prompt_preview=ptxt[:80],
                        skipped=True,
                    )
                elif self._try_register_existing_clause_png(job_id, out_path, comfy, bundle):
                    paths.append(out_path)
                    log.info(
                        "image_done",
                        job_id=job_id,
                        index=i + 1,
                        total=total,
                        backend="comfy",
                        prompt_preview=ptxt[:80],
                        recovered=True,
                    )
                else:
                    comfy.generate_one(bundle, ptxt, out_path)
                    self._store.add_artifact(
                        job_id,
                        PipelineStage.images,
                        ArtifactType.image_png,
                        out_path,
                        meta={},
                    )
                    paths.append(out_path)
                    log.info(
                        "image_done",
                        job_id=job_id,
                        index=i + 1,
                        total=total,
                        backend="comfy",
                        prompt_preview=ptxt[:80],
                    )
                self._consume_pause_after_image(job_id)

        if not incremental_image_artifacts:
            for p in paths:
                self._store.add_artifact(
                    job_id,
                    PipelineStage.images,
                    ArtifactType.image_png,
                    p,
                    meta={},
                )

        # Ensure the shared outro image exists (generated once, reused across all jobs).
        if self._settings.outro_image_enabled and self._settings.end_plate_enabled:
            self._ensure_shared_outro_image()

        log.info("images_done", job_id=job_id, total=total, backend=backend)

    def run_tts(self, job_id: str) -> None:
        plan = self._load_plan(job_id)
        out = self._job_dir(job_id) / "narration.wav"
        backend = self._settings.tts_backend.lower().strip()
        log.info(
            "tts_start",
            job_id=job_id,
            backend=backend,
            words=len(plan.full_script.split()),
            script_preview=plan.full_script[:120],
        )
        if backend in ("kokoro", "kokoro_http"):
            from shorts_pipeline.tts_worker.kokoro import KokoroTTSClient
            KokoroTTSClient(self._settings).synthesize_wav(plan.full_script, out)
        else:
            raise RuntimeError(
                f"Unknown tts_backend {backend!r}. "
                "Set SHORTS_TTS_BACKEND to 'kokoro' or 'kokoro_http'."
            )
        self._store.add_artifact(
            job_id,
            PipelineStage.tts,
            ArtifactType.narration_wav,
            out,
            meta={"tts_backend": backend},
        )
        log.info("tts_done", job_id=job_id, backend=backend)

    def run_align(self, job_id: str) -> None:
        log.info("align_start", job_id=job_id)
        plan = self._load_plan(job_id)
        wav = self._store.get_latest_artifact(job_id, PipelineStage.tts, ArtifactType.narration_wav)
        if wav is None:
            raise RuntimeError("missing narration wav artifact")
        # TTS synthesises full_script; using clause texts causes word mismatches
        # (e.g. "rumbles" vs "rumbled") that exhaust all whisper spans on one token.
        reference = plan.full_script
        aligner = load_aligner(self._settings)
        words = aligner.align_words(Path(wav.path), reference)
        ranges = clause_time_ranges_from_words(plan, words)
        jd = self._job_dir(job_id)

        # Measure actual narration duration so the last subtitle event can be
        # extended to cover the audio tail (TTS silence / fade at the very end).
        narration_end_s: float | None = None
        try:
            with wave.open(str(Path(wav.path)), "rb") as wf:
                narration_end_s = wf.getnframes() / float(wf.getframerate())
        except Exception:
            pass

        range_max = max((float(r[1]) for r in ranges), default=0.0)
        word_end = max((float(w.end_s) for w in words), default=0.0)
        if narration_end_s is not None:
            narration_end_s = max(narration_end_s, range_max, word_end)
            narration_end_s += float(self._settings.narration_tail_silence_pad_s or 0.0)
        elif range_max > 0 or word_end > 0:
            narration_end_s = max(range_max, word_end)
            narration_end_s += float(self._settings.narration_tail_silence_pad_s or 0.0)

        timings_path = jd / "clause_timings.json"
        timings_path.write_text(json.dumps(ranges, indent=2), encoding="utf-8")
        self._store.add_artifact(
            job_id,
            PipelineStage.align,
            ArtifactType.clause_timings_json,
            timings_path,
            meta={},
        )

        ass_path = jd / "subtitles.ass"
        build_ass_karaoke(plan, words, self._settings, ass_path, narration_end_s=narration_end_s)
        self._store.add_artifact(
            job_id,
            PipelineStage.align,
            ArtifactType.subtitles_ass,
            ass_path,
            meta={},
        )
        log.info("align_done", job_id=job_id, clause_count=len(ranges))

    def run_i2v(self, job_id: str) -> None:
        """Generate Wan 2.2 I2V MP4 clips — one per clause, timed to align ranges."""
        if not self._settings.i2v_enabled:
            log.info("i2v_skipped_disabled", job_id=job_id)
            return

        rec = self._store.get_job(job_id)
        if rec is None:
            raise ValueError("job not found")
        plan = self._load_plan(job_id)
        niche = plan.niche or rec.config_snapshot.topic_type

        image_paths, _, _, _, timings_path = self._load_render_artifacts(job_id, plan)
        ranges = self._load_ranges(timings_path, expected=len(plan.clauses))

        wf_name = self._settings.i2v_workflow_name
        bundle_path = (self._settings.workflows_dir / f"{wf_name}.json").resolve()
        bundle = load_i2v_bundle(bundle_path)
        client = make_i2v_client(self._settings)

        out_dir = self._job_dir(job_id) / "videos"
        out_dir.mkdir(parents=True, exist_ok=True)
        max_dur = float(self._settings.i2v_max_clip_duration_s)

        log.info(
            "i2v_start",
            job_id=job_id,
            total=len(plan.clauses),
            workflow=wf_name,
            niche=niche,
        )

        for i, (clause, img_path, (start_s, end_s)) in enumerate(
            zip(plan.clauses, image_paths, ranges)
        ):
            out_path = out_dir / f"clause_{i:03d}.mp4"
            if self._video_artifact_valid_for_path(job_id, out_path):
                log.info("i2v_done", job_id=job_id, index=i + 1, total=len(plan.clauses), skipped=True)
                continue

            duration_s = min(max_dur, max(0.5, end_s - start_s))
            motion = resolve_motion_prompt(clause, niche=niche)
            log.info(
                "i2v_generating",
                job_id=job_id,
                index=i + 1,
                total=len(plan.clauses),
                duration_s=round(duration_s, 2),
                motion_preview=motion[:80],
            )
            client.generate_clip(
                bundle,
                image_path=img_path,
                motion_prompt=motion,
                duration_s=duration_s,
                out_path=out_path,
            )
            self._store.add_artifact(
                job_id,
                PipelineStage.i2v,
                ArtifactType.video_mp4,
                out_path,
                meta={"clause_index": i, "duration_s": duration_s},
            )
            log.info(
                "i2v_done",
                job_id=job_id,
                index=i + 1,
                total=len(plan.clauses),
                out=out_path.name,
            )

        log.info("i2v_stage_done", job_id=job_id, total=len(plan.clauses))

    def _video_artifact_valid_for_path(self, job_id: str, out_path: Path) -> bool:
        arts = self._store.get_artifacts_for_stage(job_id, PipelineStage.i2v, ArtifactType.video_mp4)
        resolved = out_path.resolve()
        for a in arts:
            if Path(a.path).resolve() == resolved:
                return verify_artifact_path(Path(a.path), a.sha256)
        return False

    def _load_i2v_video_paths(self, job_id: str, plan: NarrationPlan) -> list[Path | None]:
        if not self._settings.i2v_enabled:
            return [None] * len(plan.clauses)
        arts = self._store.get_artifacts_for_stage(
            job_id, PipelineStage.i2v, ArtifactType.video_mp4
        )
        if not arts:
            return [None] * len(plan.clauses)
        sorted_arts = sort_clause_mp4_artifacts(arts)
        by_idx = {
            clause_index_from_video_path(str(a.path)): Path(a.path) for a in sorted_arts
        }
        return [by_idx.get(i) for i in range(len(plan.clauses))]

    # ── Render helpers ────────────────────────────────────────────────────────

    def _load_render_artifacts(self, job_id: str, plan: NarrationPlan) -> tuple[
        list[Path], Path | None, Path, Path, Path
    ]:
        """Gather all artifacts needed by the render stage.

        Returns: (clause_image_paths, outro_image_path_or_None, ass_path, wav_path, timings_path)
        Raises RuntimeError if any required artifact is missing or count mismatches.
        """
        imgs_all = self._store.get_artifacts_for_stage(
            job_id, PipelineStage.images, ArtifactType.image_png
        )
        # Exclude any legacy per-job outro.png (old schema) — shared image is used instead
        clause_imgs = [a for a in imgs_all if Path(a.path).name != "outro.png"]
        if len(clause_imgs) != len(plan.clauses):
            raise RuntimeError(
                f"image set mismatch: {len(clause_imgs)} images for {len(plan.clauses)} clauses"
            )
        clause_imgs_sorted = sort_clause_png_artifacts(clause_imgs)

        ass_art = self._store.get_latest_artifact(
            job_id, PipelineStage.align, ArtifactType.subtitles_ass
        )
        wav_art = self._store.get_latest_artifact(
            job_id, PipelineStage.tts, ArtifactType.narration_wav
        )
        timings_art = self._store.get_latest_artifact(
            job_id, PipelineStage.align, ArtifactType.clause_timings_json
        )
        if ass_art is None or wav_art is None or timings_art is None:
            raise RuntimeError("missing align/tts artifacts for render")

        # Use the single shared outro image (assets/outro.png); falls back to None
        # which makes the renderer blur the last clause clip instead.
        outro_path: Path | None = None
        shared_outro = self._shared_outro_image_path()
        if shared_outro.is_file():
            outro_path = shared_outro

        return (
            [Path(i.path) for i in clause_imgs_sorted],
            outro_path,
            Path(ass_art.path),
            Path(wav_art.path),
            Path(timings_art.path),
        )

    def _load_ranges(self, timings_path: Path, expected: int) -> list[tuple[float, float]]:
        raw_ranges: list[list[float]] = json.loads(timings_path.read_text(encoding="utf-8"))
        ranges = [(float(r[0]), float(r[1])) for r in raw_ranges]
        if len(ranges) != expected:
            raise RuntimeError(
                f"clause_timings has {len(ranges)} rows but plan has {expected} clauses "
                "(editor would drop extras). Re-run align after fixing plan/full_script, or "
                "delete clause_timings.json and subtitles.ass for this job."
            )
        return ranges

    def _compute_narration_duration(
        self,
        job_id: str,
        wav_path: Path,
        ranges: list[tuple[float, float]],
    ) -> float:
        """Mux length must cover the real WAV; tail_pad extends it further if set."""
        range_max = max((end for _, end in ranges), default=0.0)
        wav_duration_s: float | None = None
        try:
            with wave.open(str(wav_path), "rb") as wf:
                wav_duration_s = wf.getnframes() / float(wf.getframerate())
        except Exception:
            pass

        narration_duration_s = max(range_max, wav_duration_s or 0.0)
        if wav_duration_s is not None and wav_duration_s > range_max + 0.05:
            log.info(
                "render_narration_tail_extend",
                job_id=job_id,
                range_max_s=round(range_max, 3),
                wav_duration_s=round(wav_duration_s, 3),
                using_s=round(narration_duration_s, 3),
            )

        tail_pad = float(self._settings.narration_tail_silence_pad_s or 0.0)
        if tail_pad > 0.0:
            # Footgun: if this changed AFTER align, the ASS last-event may not
            # reach the new tail. Re-run align after changing this value.
            narration_duration_s += tail_pad
            log.info(
                "render_narration_tail_pad",
                job_id=job_id,
                pad_s=round(tail_pad, 3),
                narration_s=round(narration_duration_s, 3),
                note="re-run align if tail_pad changed after subtitles were built",
            )
        return narration_duration_s

    def _build_render_settings(
        self,
        job_id: str,
        rec: Any,
        narration_duration_s: float,
    ) -> Settings:
        """Build per-job render Settings: copy job config + dynamic outro duration."""
        # Per-job UI snapshot OR .env: either can enable watermark / end plate so
        # .env changes apply to re-renders without re-creating jobs, while explicit
        # UI-on still works when .env is off.
        wm_on = self._settings.watermark_enabled or rec.config_snapshot.watermark_enabled
        ep_on = self._settings.end_plate_enabled or rec.config_snapshot.end_plate_enabled
        render_settings = self._settings.model_copy(
            update={
                "watermark_enabled": wm_on,
                "end_plate_enabled": ep_on,
                "overlay_enabled": rec.config_snapshot.overlay_enabled,
            }
        )

        if render_settings.end_plate_enabled:
            body_s = narration_duration_s + render_settings.last_frame_breathe_s
            hard_cap = render_settings.render_max_shorts_duration_s
            target = min(render_settings.outro_target_total_s, hard_cap)
            raw_outro = target - body_s
            outro_dur = max(
                render_settings.outro_min_duration_s,
                min(render_settings.outro_max_duration_s, raw_outro),
            )
            # Safety: never let outro push total past the hard cap
            outro_dur = min(outro_dur, max(0.0, hard_cap - body_s))
            render_settings = render_settings.model_copy(
                update={"end_plate_duration_s": outro_dur}
            )
            log.info(
                "outro_duration_computed",
                job_id=job_id,
                body_s=round(body_s, 2),
                outro_s=round(outro_dur, 2),
                total_s=round(body_s + outro_dur, 2),
                hard_cap_s=hard_cap,
            )
        return render_settings

    def _render_long_version(self, job_id: str, short_mp4: Path) -> None:
        """Produce final_long.mp4 at render_long_version_speed (default 0.9×).

        Uses FFmpeg setpts + atempo to slow both video and audio proportionally
        without re-encoding images — pitch is preserved by atempo's WSOLA algorithm.
        Failure is non-fatal: the job completes with the Shorts version regardless.
        """
        speed = self._settings.render_long_version_speed
        out_path = short_mp4.parent / "final_long.mp4"
        log.info("render_long_start", job_id=job_id, speed=speed, out=str(out_path))

        # setpts=PTS/speed stretches frame timestamps (speed<1 → longer duration).
        # atempo=speed slows audio to match (WSOLA, no pitch change).
        cmd = [
            self._settings.ffmpeg_path, "-y",
            "-i", str(short_mp4),
            "-filter_complex",
            f"[0:v]setpts=PTS/{speed}[v];[0:a]atempo={speed}[a]",
            "-map", "[v]",
            "-map", "[a]",
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k",
            str(out_path),
        ]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=300
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr[-800:] if result.stderr else "ffmpeg error")
            self._store.add_artifact(
                job_id,
                PipelineStage.render,
                ArtifactType.final_long_mp4,
                out_path,
                meta={"speed": speed},
            )
            log.info("render_long_done", job_id=job_id, path=str(out_path))
        except Exception as exc:
            log.warning(
                "render_long_failed",
                job_id=job_id,
                error=str(exc)[:400],
                error_type=type(exc).__name__,
            )

    def run_render(self, job_id: str) -> None:
        rec = self._store.get_job(job_id)
        if rec is None:
            raise ValueError("job not found")
        plan = self._load_plan(job_id)

        image_paths, outro_image_path, ass_path, wav_path, timings_path = (
            self._load_render_artifacts(job_id, plan)
        )
        video_paths = self._load_i2v_video_paths(job_id, plan)
        ranges = self._load_ranges(timings_path, expected=len(plan.clauses))
        narration_duration_s = self._compute_narration_duration(job_id, wav_path, ranges)
        render_settings = self._build_render_settings(job_id, rec, narration_duration_s)

        tr_seed = render_settings.transition_random_seed
        if render_settings.randomize_clip_transitions and tr_seed is None:
            tr_seed = int(hashlib.sha256(job_id.encode("utf-8")).hexdigest()[:8], 16)

        edit = build_edit_plan_from_ranges(
            plan,
            image_paths,
            ranges,
            narration_duration_s,
            bgm_path=Path(rec.config_snapshot.bgm_path),
            video_paths=video_paths,
            snap_to_bgm_beats=render_settings.snap_to_bgm_beats,
            run_face_detection=True,
            randomize_transitions=render_settings.randomize_clip_transitions,
            transition_random_seed=tr_seed if render_settings.randomize_clip_transitions else None,
        )

        log.info(
            "render_start",
            job_id=job_id,
            clause_count=len(plan.clauses),
            has_outro_image=outro_image_path is not None,
            i2v_clips=sum(1 for p in video_paths if p is not None and p.is_file()),
        )
        req = RenderRequest(
            edit=edit,
            narration_wav=wav_path,
            ass_path=ass_path,
            out_mp4=self._job_dir(job_id) / "final.mp4",
            narration_duration_s=narration_duration_s,
            outro_image_path=outro_image_path,
        )
        render_short(req, render_settings)
        self._store.add_artifact(
            job_id,
            PipelineStage.render,
            ArtifactType.final_mp4,
            req.out_mp4,
            meta={},
        )
        log.info("render_done", job_id=job_id)

        if render_settings.render_long_version_enabled:
            self._render_long_version(job_id, req.out_mp4)

    def run_rescript(self, job_id: str, *, user_feedback: str | None = None) -> None:
        """Regenerate the plan (with optional producer feedback), then clear tts/align/render/publish
        artifacts so the next ``resume_job`` call re-runs those stages with the new script.

        Images are preserved — the new script must still have exactly 14 clauses.
        """
        rec = self._store.get_job(job_id)
        if rec is None:
            raise ValueError("job not found")
        cfg = rec.config_snapshot

        log.info(
            "rescript_start",
            job_id=job_id,
            figure=cfg.figure_name,
            has_feedback=bool(user_feedback),
        )

        client = build_planner_client(self._settings)
        plan = client.generate_plan(
            cfg.figure_name,
            topic_type=cfg.topic_type,
            language=cfg.language,
            user_feedback=user_feedback,
        )

        jd = self._job_dir(job_id)
        jd.mkdir(parents=True, exist_ok=True)
        plan_path = jd / "plan.json"
        plan_path.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
        self._store.add_artifact(
            job_id,
            PipelineStage.plan,
            ArtifactType.plan_json,
            plan_path,
            meta={"figure": cfg.figure_name, "rescript": True},
        )

        image_arts = self._store.get_artifacts_for_stage(
            job_id, PipelineStage.images, ArtifactType.image_png
        )
        has_valid_images = len(image_arts) > 0 and all(
            verify_artifact_path(Path(a.path), a.sha256) for a in image_arts
        )

        if has_valid_images:
            self._store.delete_artifacts_from_stage(job_id, PipelineStage.tts)
            self._store.update_job_progress(
                job_id,
                status=JobStatus.pending,
                current_stage=PipelineStage.tts,
                last_completed_stage=PipelineStage.images,
                clear_error=True,
            )
        else:
            self._store.delete_artifacts_from_stage(job_id, PipelineStage.images)
            self._store.update_job_progress(
                job_id,
                status=JobStatus.pending,
                current_stage=PipelineStage.images,
                last_completed_stage=PipelineStage.plan,
                clear_error=True,
            )

        log.info(
            "rescript_done",
            job_id=job_id,
            figure=cfg.figure_name,
            words=len(plan.full_script.split()),
            keep_images=has_valid_images,
        )

    def run_publish(self, job_id: str) -> None:
        """Generate the YouTube Shorts publishing package (titles, description,
        tags, thumbnail brief, CTR strategy).

        Failure policy: this stage NEVER fails the pipeline. If every LLM tier
        is unavailable, we fall back to a deterministic rule-based package so
        the operator always has something to upload. The video is the primary
        deliverable; metadata is a last-mile convenience."""
        rec = self._store.get_job(job_id)
        if rec is None:
            raise ValueError("job not found")

        plan = self._load_plan(job_id)
        figure = rec.config_snapshot.figure_name or plan.historical_figure

        log.info(
            "publish_start",
            job_id=job_id,
            figure=figure,
            backend=self._settings.planner_backend,
        )

        package, source = self._generate_publishing_package(figure, plan)
        self._write_publishing_artifacts(job_id, figure, package, source=source)

        log.info(
            "publish_done",
            job_id=job_id,
            figure=figure,
            source=source,
            title_main=package.titles.main,
            tag_count=len(package.tags),
            hashtag_count=len(package.hashtags),
        )

        # ── Telegram auto-send ────────────────────────────────────────────────
        # Non-fatal: a Telegram failure never marks the job as failed.
        try:
            from shorts_pipeline.tg_notify import enqueue_send_job, send_job
            from shorts_pipeline.tg_topics import resolve_job_niche
            jd = self._job_dir(job_id)
            niche = resolve_job_niche(
                jd,
                plan_niche=plan.niche,
                default_niche=self._settings.tg_default_niche,
            )
            tg_kwargs = dict(
                api_id=self._settings.tg_api_id,
                api_hash=self._settings.tg_api_hash,
                session_path=self._settings.tg_session_path,
                target_chat=self._settings.tg_target_chat,
                job_dir=jd,
                package_json=jd / "publish_package.json",
                send_archive=self._settings.tg_send_archive,
                archive_max_mb=self._settings.tg_archive_max_mb,
                niche=niche,
                topics_path=self._settings.tg_topics_path,
                forum_topics_enabled=self._settings.tg_forum_topics_enabled,
                max_retries=self._settings.tg_send_max_retries,
            )
            if self._settings.tg_async_send:
                enqueue_send_job(**tg_kwargs)
            else:
                send_job(**tg_kwargs)
        except Exception as _tg_exc:
            log.warning("tg_notify_hook_error: %s", _tg_exc)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _generate_publishing_package(
        self,
        figure: str,
        plan: NarrationPlan,
    ) -> tuple[PublishingPackage, str]:
        """Try the configured LLM backend; fall back to a rule-based package.
        Returns (package, source) where source is 'llm' or 'fallback'."""
        try:
            client = build_publisher_client(self._settings)
            package = client.generate_package(figure, plan)
            return package, "llm"
        except Exception as exc:
            log.warning(
                "publish_llm_failed_using_fallback",
                figure=figure,
                error=str(exc)[:400],
                error_type=type(exc).__name__,
            )
            return build_fallback_package(figure, plan), "fallback"

    def _write_publishing_artifacts(
        self,
        job_id: str,
        figure: str,
        package: PublishingPackage,
        *,
        source: str,
    ) -> None:
        """Write publish_package.json (tracked artifact) plus a paste-ready .txt sidecar."""
        jd = self._job_dir(job_id)
        jd.mkdir(parents=True, exist_ok=True)

        json_path = jd / "publish_package.json"
        json_path.write_text(package.model_dump_json(indent=2), encoding="utf-8")

        txt_path = jd / "publish_package.txt"
        txt_path.write_text(render_package_text(figure, package), encoding="utf-8")

        self._store.add_artifact(
            job_id,
            PipelineStage.publish,
            ArtifactType.publish_package_json,
            json_path,
            meta={"source": source, "txt_sidecar": str(txt_path)},
        )

    def _load_plan(self, job_id: str) -> NarrationPlan:
        art = self._store.get_latest_artifact(
            job_id, PipelineStage.plan, ArtifactType.plan_json
        )
        if art is None:
            raise RuntimeError("missing plan artifact")
        ctx = {"allow_figure_name": bool(getattr(self._settings, "image_prompts_include_figure_name", False))}
        return NarrationPlan.model_validate_json(
            Path(art.path).read_text(encoding="utf-8"),
            context=ctx,
        )


def orchestrator_stage_handler(settings: Settings, store: JobStore):
    """Return a namespace object compatible with StageRunner handler protocol."""
    orch = PipelineOrchestrator(settings, store)

    class _H:
        def run_plan(self, job_id: str) -> None:
            orch.run_plan(job_id)

        def run_images(self, job_id: str) -> None:
            orch.run_images(job_id)

        def run_tts(self, job_id: str) -> None:
            orch.run_tts(job_id)

        def run_align(self, job_id: str) -> None:
            orch.run_align(job_id)

        def run_i2v(self, job_id: str) -> None:
            orch.run_i2v(job_id)

        def run_render(self, job_id: str) -> None:
            orch.run_render(job_id)

        def run_publish(self, job_id: str) -> None:
            orch.run_publish(job_id)

    return _H()
