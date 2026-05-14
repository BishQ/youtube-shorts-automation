"""Application settings: env + optional UI overrides (non-secrets)."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SHORTS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    data_dir: Path = Field(default=Path("./data"))

    # ── Planner backend selector ─────────────────────────────────────────────
    # "gemini"  → Gemini API only. On daily quota exhaustion the pipeline pauses
    #             until midnight Pacific Time, then retries automatically.
    # "hybrid"  → Try Gemini every time; on rate/daily quota errors fall back to
    #             DeepSeek for that request. When Gemini works again, the next
    #             job uses Gemini automatically (no manual switch).
    planner_backend: str = "gemini"

    # Gemini (planner_backend="gemini")
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.5-flash"
    # Comma-separated model ids, tried in order when the previous hits quota / RPM.
    # If unset, only gemini_model is used. Example:
    #   SHORTS_GEMINI_MODEL_CHAIN=gemini-2.5-flash,gemini-3.1-flash-lite
    gemini_model_chain: str | None = None
    gemini_timeout_s: float = 60.0

    @field_validator("gemini_api_key", mode="before")
    @classmethod
    def _strip_gemini_api_key(cls, v: object) -> object:
        if v is None:
            return None
        if isinstance(v, str):
            s = v.strip()
            return s if s else None
        return v

    def gemini_models_ordered(self) -> list[str]:
        """Planner tries these Gemini model ids in order before waiting or DeepSeek."""
        raw = (self.gemini_model_chain or "").strip()
        if raw:
            out = [p.strip() for p in raw.split(",") if p.strip()]
            if out:
                return out
        single = (self.gemini_model or "").strip()
        return [single] if single else []

    # DeepSeek (planner_backend="hybrid" — fallback when Gemini quota / capacity fails)
    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    # deepseek-chat = DeepSeek-V3 (fast, cheap). Use deepseek-reasoner for DeepSeek-R1.
    deepseek_model: str = "deepseek-chat"
    deepseek_timeout_s: float = 120.0
    # Max transient-error retries inside DeepSeekPlannerClient before giving up.
    deepseek_transient_retries: int = 3

    # ── Hybrid failover tuning ────────────────────────────────────────────────
    # Seconds Gemini stays "degraded" after all chain models fail before the
    # router retries it as the primary.  Set to 0 to always try Gemini first.
    hybrid_gemini_cooldown_s: int = 600
    # Max transient HTTP retries per Gemini model in hybrid mode (fast-fail so the
    # router can switch to the next model / DeepSeek quickly).
    hybrid_gemini_transient_retries: int = 2

    @field_validator("deepseek_api_key", mode="before")
    @classmethod
    def _strip_deepseek_api_key(cls, v: object) -> object:
        if v is None:
            return None
        if isinstance(v, str):
            s = v.strip()
            return s if s else None
        return v

    # ── Image backend selector ────────────────────────────────────────────────
    # "comfy"  → All images via local ComfyUI (default).
    # "hybrid" → Clauses 1,3,5,7,9,last via Flux Pro API (lower-res → upscaled
    #             by ComfyUI); clauses 2,4,6,8,10,second-to-last via local ComfyUI
    #             Flux Schnell GGUF.  Saves API cost while keeping quality high.
    # "hybrid_grok_bookends" → First + last clause (and optional extras) via xAI
    #             Grok Imagine (prompt prefixed with figure name for likeness);
    #             all other clauses via local ComfyUI. Requires SHORTS_GROK_API_KEY.
    # "hybrid_grok_flux" → Same Grok slots as hybrid_grok_bookends, plus Flux API
    #             (Together or BFL) for the remaining hybrid "API" clauses (evens
    #             + last except second-to-last), rest local ComfyUI. Requires both
    #             SHORTS_GROK_API_KEY and SHORTS_FLUX_API_KEY.
    image_backend: str = "comfy"

    comfy_base_url: str = "http://127.0.0.1:8188"
    comfy_timeout_s: float = 600.0
    comfy_workflow_name: str = "flux_schnell_local"
    comfy_upscale_workflow_name: str = "upscale_4x_ultrasharp"
    workflows_dir: Path = Field(default=Path("./workflows"))
    comfy_poll_interval_s: float = 1.0
    comfy_max_polls: int = 1200
    # When ComfyUI is not running yet (connection refused), retry the HTTP call instead of failing.
    # 0 on max wait = keep trying until Comfy accepts TCP (cancel the job to stop).
    comfy_connect_retry_interval_s: float = Field(default=2.5, ge=0.5, le=120.0)
    comfy_connect_max_wait_s: float = Field(default=0.0, ge=0.0)
    # Used when reconciling/importing a job from disk if BGM is not in the JSON body and
    # import_meta.json has no bgm_path. Also checks ./extra tools/bgm.mp3 next to the repo.
    default_bgm_path: str | None = Field(default=None, max_length=4096)

    @field_validator("default_bgm_path", mode="before")
    @classmethod
    def _strip_default_bgm_path(cls, v: object) -> object:
        if v is None:
            return None
        if isinstance(v, str):
            s = v.strip()
            return s if s else None
        return v

    # ── Remote Flux images (required when image_backend=hybrid) ───────────────
    # flux_api_provider:
    #   "bfl"      → Black Forest Labs API (X-Key, async poll). Key: api.bfl.ml
    #   "together" → Together AI (Bearer, sync). Key: together.ai — FLUX.2 [pro] etc.
    flux_api_provider: str = "bfl"
    flux_api_key: str | None = None
    flux_api_base_url: str = "https://api.us1.bfl.ai"
    together_api_base_url: str = "https://api.together.ai"
    # Model slug depends on provider:
    #   bfl:       flux-pro-1.1, flux-pro-2, …
    #   together:  black-forest-labs/FLUX.2-pro, black-forest-labs/FLUX.2-dev, …
    flux_api_model: str = "flux-pro-1.1"
    # Lower resolution saves API cost; upscaler restores quality afterwards
    flux_api_width: int = 576
    flux_api_height: int = 1024
    flux_api_poll_interval_s: float = 2.0
    flux_api_max_polls: int = 90   # BFL only: 90 × interval max wait per image
    flux_together_timeout_s: float = 300.0  # Together: single POST timeout (image gen)
    # Together-only: FLUX.2 docs allow 256–1920 per side; moderation may return 400/422.
    flux_together_disable_safety_checker: bool = False  # use only if your ToS allows it
    flux_together_http_retries: int = Field(
        default=2,
        ge=0,
        le=8,
        description="Extra POST attempts per payload (400/422 backoff + 429/5xx)",
    )

    # ── xAI Grok Imagine (image_backend=hybrid_grok_bookends) ─────────────────
    grok_api_key: str | None = None
    grok_api_base_url: str = "https://api.x.ai/v1"
    grok_image_model: str = "grok-imagine-image-quality"
    grok_timeout_s: float = 180.0
    # Comma-separated 0-based clause indices for extra Grok shots (e.g. "6,7"
    # for emotional mid-closes). First (0) and last are always Grok in this mode.
    grok_extra_clause_indices: str | None = None

    @field_validator("grok_api_key", mode="before")
    @classmethod
    def _strip_grok_api_key(cls, v: object) -> object:
        if v is None:
            return None
        if isinstance(v, str):
            s = v.strip()
            return s if s else None
        return v

    @field_validator("flux_api_key", mode="before")
    @classmethod
    def _strip_flux_api_key(cls, v: object) -> object:
        if v is None:
            return None
        if isinstance(v, str):
            s = v.strip()
            return s if s else None
        return v

    # ── TTS backend selector ─────────────────────────────────────────────────
    # "kokoro"       → direct Python API (pip install kokoro soundfile numpy)
    # "kokoro_http"  → kokoro-fastapi HTTP server
    # "fish"         → Fish Speech HTTP (legacy)
    tts_backend: str = "kokoro"

    # Kokoro shared options (used by both modes)
    kokoro_voice: str = "am_adam"
    kokoro_speed: float = 1.0
    kokoro_lang_code: str = "a"   # 'a'=American English, 'b'=British English

    # Kokoro HTTP mode (tts_backend="kokoro_http")
    kokoro_http_base_url: str = "http://127.0.0.1:8880"
    kokoro_http_timeout_s: float = 120.0

    # Fish Speech (tts_backend="fish", kept for backward compat)
    fish_base_url: str = "http://127.0.0.1:5000"
    fish_timeout_s: float = 120.0
    fish_tts_path: str = "/v1/tts"
    fish_reference_id: str | None = None

    gpu_id: int = 0

    aligner_backend: str = "faster_whisper"
    whisper_model_size: str = "small"   # better accuracy than base, still fast on CPU
    whisper_device: str = "cpu"         # use "auto" to probe CUDA, "cuda" to force GPU

    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"

    video_width: int = 1080
    video_height: int = 1920
    video_fps: int = 30
    render_duration_tolerance_s: float = Field(default=0.75, ge=0.0, le=5.0)
    render_fps_tolerance: float = Field(default=0.25, ge=0.0, le=2.0)
    render_max_shorts_duration_s: float = Field(default=60.0, ge=1.0, le=600.0)
    render_min_size_bytes: int = Field(default=100_000, ge=0)
    # Long version: after Shorts final.mp4, produce a slower copy for non-Shorts platforms.
    # 0.9 = 90% speed → a 57s Short becomes ~63s; a 59.3s Short becomes ~65.9s.
    render_long_version_enabled: bool = True
    render_long_version_speed: float = Field(default=0.9, ge=0.5, le=1.0)

    # Each clip cut (except the opening frame) picks a random FFmpeg ``xfade``
    # preset from the full catalog (~58). Set false to restore the fixed ladder
    # in ``editor/__init__.py`` (paint_splatter / vr_light_rays / …).
    randomize_clip_transitions: bool = True
    # When set, fixes the RNG order for reproducible renders; when unset with
    # ``randomize_clip_transitions``, the render stage derives a seed from job_id.
    transition_random_seed: int | None = None

    watermark_enabled: bool = False
    watermark_opacity: float = 0.15
    watermark_png_path: Path | None = None
    end_plate_enabled: bool = True

    # ── Atmospheric overlay (snow / dust / particles) ────────────────────────
    # A looping video composited onto the final frame using "screen" blend so
    # black areas vanish and bright particles (snow, embers) layer on top.
    overlay_enabled: bool = False
    overlay_video_path: Path | None = None
    overlay_opacity: float = Field(default=0.55, ge=0.0, le=1.0)

    log_level: str = "INFO"
    log_json: bool = False

    ass_font_name: str = "Arial Black"    # bolder face for viral Shorts look
    ass_font_size: int = 92              # strong but less likely to cover faces
    ass_bold: bool = True
    ass_outline: int = 7                 # thick mobile-readable outline
    ass_shadow: int = 4                  # controlled drop-shadow depth
    ass_margin_v: int = 235              # off-bottom safe zone
    ass_primary_colour: str = "&H00FFFFFF"    # base text colour (white)
    ass_secondary_colour: str = "&H55FFFFFF"  # unused
    ass_outline_colour: str = "&H00000000"    # black text outline / box border
    ass_back_colour: str = "&H00000000"       # shadow colour (BorderStyle=1)
    # BorderStyle: 1 = classic outline+shadow (universally supported)
    ass_border_style: int = 1

    # ── Word-highlight / karaoke subtitle style ──────────────────────────────
    # Active word gets a bright-coloured background box (CapCut "Fill" style).
    # subtitle_highlight_color : background colour of the active word box
    #   yellow = &H0000FFFF  |  gold = &H001090FF  |  cyan = &H00FFFF00
    # subtitle_active_text_colour : text colour drawn ON the highlight background
    # subtitle_inactive_alpha     : alpha of inactive word box background (00=opaque)
    # subtitle_chunk_size         : words shown at once (3 recommended)
    subtitle_highlight_color: str = "&H0000FFFF"        # yellow active box
    subtitle_active_text_colour: str = "&H00000000"     # black text on yellow box
    subtitle_inactive_alpha: str = "&H55"               # readable inactive words
    subtitle_chunk_size: int = 3
    # Emphasis word uses a vivid red-orange background box + white text
    subtitle_emphasis_glow: str = "&H001040FF"          # vivid red-orange box

    # Maximum total characters (including spaces) across all words in one visible
    # group.  Groups are split by char count first, then by subtitle_chunk_size.
    # 0 = disable (use word-count only).  12 keeps lines short on mobile.
    subtitle_max_chunk_chars: int = 16
    # Minimum seconds the last event of a group stays on screen after the
    # spoken word ends.  Prevents flicker on short/fast words.  1.2 s is the
    # Netflix / BBC accessibility standard.
    subtitle_min_group_duration_s: float = 1.2

    # ── Asset directories ────────────────────────────────────────────────────
    assets_dir: Path = Field(default=Path("./assets"))
    luts_dir: Path = Field(default=Path("./assets/luts"))
    sfx_dir: Path = Field(default=Path("./assets/sfx"))

    # Explicit SFX file paths (resolved against sfx_dir if relative)
    sfx_whoosh_path: Path | None = None
    sfx_low_rumble_path: Path | None = None
    sfx_impact_path: Path | None = None
    sfx_paper_flutter_path: Path | None = None
    sfx_crowd_cheer_path: Path | None = None
    sfx_sword_clash_path: Path | None = None
    sfx_horse_gallop_path: Path | None = None
    sfx_fire_crackle_path: Path | None = None
    sfx_thunder_crack_path: Path | None = None
    sfx_crowd_murmur_path: Path | None = None

    # ── Visual effect strengths ──────────────────────────────────────────────
    # grain: noise filter alls value (0 = off, 8 = subtle, 20 = heavy)
    grain_strength: int = Field(default=8, ge=0, le=30)
    # vignette: FFmpeg vignette filter angle (radians; ~0.628 = PI/5 = moderate)
    vignette_angle: float = Field(default=0.628, ge=0.0, le=3.14)

    # ── BGM levels ──────────────────────────────────────────────────────────
    # Linear gain applied *after* sidechaincompress (duck-under-voice). Defaults
    # are tuned so the bed stays audible under TTS; raise toward 1.0 if you want
    # music louder, or use ``bgm_volume_narration_db`` instead.
    bgm_volume_narration: float = Field(default=0.48, ge=0.0, le=1.0)
    # If set, applied as FFmpeg ``volume=XdB`` after sidechain (e.g. -4.0 boosts
    # vs very quiet linear-only mixes). When None, ``bgm_volume_narration`` is used.
    bgm_volume_narration_db: float | None = Field(default=None)
    # Sidechain: narration must exceed this amplitude before BGM ducks (higher =
    # less constant ducking on breath/noise).
    bgm_sidechain_threshold: float = Field(default=0.055, ge=0.001, le=0.5)
    # Lower ratio = gentler duck when narration is loud (music pumps up more).
    bgm_sidechain_ratio: float = Field(default=3.5, ge=1.0, le=20.0)

    # Silence appended after the spoken WAV in the narration timeline (does not
    # re-run TTS). Extends last-image pacing and mux length (e.g. +4.5s on a 54s
    # voiceover → ~58.5s body before breathe/end plate). Re-run align after
    # changing so ASS last events stretch through the tail.
    narration_tail_silence_pad_s: float = Field(default=0.0, ge=0.0, le=30.0)

    # ── Editor options ───────────────────────────────────────────────────────
    snap_to_bgm_beats: bool = False
    end_plate_font_size: int = 78
    # Fixed fallback; overridden at render time by dynamic computation (see orchestrator).
    end_plate_duration_s: float = 3.5
    # Dynamic outro: pad the outro so total video reaches this target (seconds).
    # outro_duration = clamp(target - narration - breathe, min, max)
    outro_target_total_s: float = 60.0
    outro_min_duration_s: float = 1.0
    outro_max_duration_s: float = 3.0
    # Subscribe / follow CTA shown beneath the end-plate question.
    end_plate_cta_text: str = "▶  SUBSCRIBE FOR MORE"
    end_plate_cta_font_size: int = 52
    end_plate_cta_colour: str = "yellow"
    # "Thanks for watching" line shown between the hook question and the CTA.
    end_plate_thanks_text: str = "THANKS FOR WATCHING"
    end_plate_thanks_font_size: int = 68
    # Seconds the last image keeps moving after narration ends (Rule 6)
    last_frame_breathe_s: float = 0.8
    # Fade-to-black duration at end of narration (Rule 6)
    fade_out_duration_s: float = 0.6

    # ── Image-prompt figure-name policy ─────────────────────────────────────
    # Flux / ComfyUI ignore real names and generate the wrong person.
    # Grok (and some fine-tuned models) can render named subjects accurately.
    # Set True when using a Grok or name-aware image backend so the planner
    # writes the figure's real name into every image_prompt instead of a
    # physical-signature workaround.  The schema name-guard is also bypassed.
    image_prompts_include_figure_name: bool = False

    # ── Outro image (Flux-generated background for the end plate) ────────────
    # ComfyUI generates one extra image per job using this prompt.
    # It appears as the end-plate background instead of the blurred last clip.
    outro_image_enabled: bool = True
    # Blur applied to the outro background when a dedicated outro image is used.
    # 0 = no blur (best for pre-designed graphics with baked-in text/art).
    # 6 = light blur (default for AI-generated outro images).
    outro_image_blur_sigma: int = Field(default=2, ge=0, le=40)
    outro_image_prompt: str = (
        "Dramatic cinematic wide shot of an ancient stone amphitheater at golden dusk, "
        "towering columns casting long shadows across worn stone floors, "
        "volumetric golden light beams piercing dark storm clouds overhead, "
        "breathtaking epic scale, hyper-realistic photography, 8K resolution, "
        "no people, no text, no watermark, cinematic color grade"
    )

    def resolve_sfx(self, name: str) -> Path | None:
        """Return absolute path for a named SFX file, searching sfx_dir if not absolute."""
        attr = f"sfx_{name}_path"
        explicit: Path | None = getattr(self, attr, None)
        if explicit is not None:
            p = explicit if explicit.is_absolute() else self.sfx_dir / explicit
            return p if p.is_file() else None
        # Auto-discover common filenames
        for candidate in (f"{name}.wav", f"{name}.mp3", f"{name}.ogg"):
            p = self.sfx_dir / candidate
            if p.is_file():
                return p
        return None

    def resolve_lut(self, lut_name: str) -> Path | None:
        """Return absolute path for a LUT .cube file, or None if not present."""
        for candidate in (f"{lut_name}.cube", f"{lut_name}.CUBE"):
            p = self.luts_dir / candidate
            if p.is_file():
                return p
        return None

    def resolve_overlay(self) -> Path | None:
        """Resolve the atmospheric overlay video (snow/dust). Falls back to
        ./assets/overlays/snow.mp4 if no explicit path is configured."""
        if self.overlay_video_path is not None and self.overlay_video_path.is_file():
            return self.overlay_video_path
        fallback = self.assets_dir / "overlays" / "snow.mp4"
        return fallback if fallback.is_file() else None


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    (s.data_dir / "jobs").mkdir(parents=True, exist_ok=True)
    return s
