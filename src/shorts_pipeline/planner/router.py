"""Planner router — Gemini chain + DeepSeek hybrid with tiered failover.

Backend selection via settings.planner_backend:

  "gemini"  → Gemini only, full model chain, waits for daily quota reset.
  "hybrid"  → Tiered failover across THREE tiers:

              Tier 0  Primary       first model in SHORTS_GEMINI_MODEL_CHAIN
              Tier 1  Secondary     remaining Gemini models in the chain
              Tier 2  Final         DeepSeek-V3/R1 (SHORTS_DEEPSEEK_API_KEY)

              Switching rules
              ───────────────
              • 503 Capacity overload on Tier N → immediately try Tier N+1.
              • 429 Rate limit on Tier N        → immediately try Tier N+1.
              • All Gemini models exhausted      → route to DeepSeek + start
                                                  SHORTS_HYBRID_GEMINI_COOLDOWN_S
                                                  cooldown (default 10 min).
              • Gemini in cooldown              → skip directly to DeepSeek.
              • After cooldown expires           → auto-recover: next call tries
                                                  Gemini primary again.
              • After successful Gemini call    → reset cooldown immediately.
              • Non-recoverable error (auth,    → propagate; no fallback.
                schema, bad API key)

              Log events
              ──────────
              [SWITCH] <model> 503_capacity — routing to <next>
              [SWITCH] <model> 429_rate_limit — routing to <next>
              [SWITCH] All Gemini models exhausted — routing to DeepSeek
              [SWITCH] Gemini in cooldown (<N>s remaining) — routing to DeepSeek
              [RECOVER] Gemini cooldown expired — primary restored
"""

from __future__ import annotations

import datetime
import re
import threading
import time
from typing import Protocol, runtime_checkable
from zoneinfo import ZoneInfo

from shorts_pipeline.config.settings import Settings
from shorts_pipeline.logging_setup import get_logger
from shorts_pipeline.planner.schema import NarrationPlan

log = get_logger(__name__)

_PT = ZoneInfo("America/Los_Angeles")
_RESET_BUFFER_S = 90  # extra margin after midnight PT before retrying

# ── Error-classification regexes ──────────────────────────────────────────────
_QUOTA_RE = re.compile(
    r"(429|quota|resource[_\s]?exhausted|rate[_\s]?limit|too[_\s]?many[_\s]?request)",
    re.IGNORECASE,
)
_PER_MINUTE_RE = re.compile(
    r"(per[_\s]?minute|per_minute_per_project|rpm\b)",
    re.IGNORECASE,
)
_CAPACITY_RE = re.compile(
    r"\b503\b|\b502\b|\b504\b|unavailable|high\s+demand|overloaded|try\s+again\s+later",
    re.IGNORECASE,
)


def _is_quota_error(exc: Exception) -> bool:
    blob = str(exc) + str(getattr(exc, "__cause__", "") or "")
    return bool(_QUOTA_RE.search(blob))


def _is_per_minute_limit(exc: Exception) -> bool:
    blob = str(exc) + str(getattr(exc, "__cause__", "") or "")
    return bool(_PER_MINUTE_RE.search(blob))


def _is_transient_capacity_error(exc: Exception) -> bool:
    """503/502/504 or Google UNAVAILABLE — temporary overload, not quota."""
    if _is_quota_error(exc):
        return False
    cur: BaseException | None = exc
    seen: set[int] = set()
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        code = getattr(cur, "code", None)
        if isinstance(code, int) and code in (502, 503, 504):
            return True
        status = getattr(cur, "status", None)
        if isinstance(status, str) and status.upper() == "UNAVAILABLE":
            return True
        cur = cur.__cause__  # type: ignore[assignment]
    blob = str(exc) + str(getattr(exc, "__cause__", "") or "")
    return bool(_CAPACITY_RE.search(blob))


def _seconds_until_daily_reset() -> float:
    """Seconds until Gemini daily quota resets at midnight Pacific Time."""
    now_pt = datetime.datetime.now(tz=_PT)
    next_midnight = (now_pt + datetime.timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return (next_midnight - now_pt).total_seconds()


# ── Provider health tracker ────────────────────────────────────────────────────

class _ProviderHealth:
    """
    Thread-safe cooldown state for a single LLM provider.

    After mark_degraded() the provider is considered unhealthy for
    ``cooldown_s`` seconds.  After that period, is_healthy() returns True
    again automatically (auto-recovery).  A successful call should call
    mark_healthy() to cancel any running cooldown immediately.
    """

    def __init__(self, cooldown_s: float) -> None:
        self._cooldown_s = cooldown_s
        self._degraded_at: float | None = None  # monotonic epoch
        self._lock = threading.Lock()

    def is_healthy(self) -> bool:
        with self._lock:
            if self._degraded_at is None:
                return True
            elapsed = time.monotonic() - self._degraded_at
            if elapsed >= self._cooldown_s:
                self._degraded_at = None  # auto-recovery
                return True
            return False

    def remaining_s(self) -> float:
        with self._lock:
            if self._degraded_at is None:
                return 0.0
            return max(0.0, self._cooldown_s - (time.monotonic() - self._degraded_at))

    def mark_degraded(self) -> None:
        with self._lock:
            if self._degraded_at is None:
                self._degraded_at = time.monotonic()

    def mark_healthy(self) -> None:
        with self._lock:
            self._degraded_at = None


# ── Module-level health singleton (lives for the whole process lifetime) ──────
# This lets cooldown state persist across multiple planner client instantiations
# within the same Python process (e.g. a multi-job batch run).
_gemini_process_health: _ProviderHealth | None = None
_gemini_health_lock = threading.Lock()


def _get_gemini_health(cooldown_s: float) -> _ProviderHealth:
    global _gemini_process_health
    with _gemini_health_lock:
        if _gemini_process_health is None:
            _gemini_process_health = _ProviderHealth(cooldown_s)
        return _gemini_process_health


# ── Protocol ──────────────────────────────────────────────────────────────────

@runtime_checkable
class PlannerClient(Protocol):
    def generate_plan(
        self,
        figure_name: str,
        *,
        topic_type: str,
        language: str,
        user_feedback: str | None = None,
    ) -> NarrationPlan: ...


# ── Pure Gemini client (no DeepSeek, waits for daily reset) ───────────────────

class _GeminiClient:
    """
    Tries each model in the chain in order.
    On quota exhaustion, waits until Gemini's daily quota resets at midnight PT.
    On per-minute limits, waits 65 s and retries.
    Does NOT fall back to DeepSeek.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def generate_plan(
        self,
        figure_name: str,
        *,
        topic_type: str,
        language: str,
        user_feedback: str | None = None,
    ) -> NarrationPlan:
        from shorts_pipeline.planner.gemini_client import GeminiPlannerClient

        models = self._settings.gemini_models_ordered()
        if not models:
            raise ValueError(
                "No Gemini model ids configured. Set SHORTS_GEMINI_MODEL or "
                "SHORTS_GEMINI_MODEL_CHAIN in .env."
            )

        while True:
            last_exc: Exception | None = None
            for idx, model_name in enumerate(models):
                try:
                    log.info(
                        "planner_gemini_attempt",
                        figure=figure_name,
                        model=model_name,
                        model_index=idx,
                    )
                    return GeminiPlannerClient(self._settings, model=model_name).generate_plan(
                        figure_name, topic_type=topic_type, language=language,
                        user_feedback=user_feedback,
                    )
                except Exception as exc:
                    last_exc = exc
                    has_next = idx + 1 < len(models)
                    if has_next and (
                        _is_transient_capacity_error(exc) or _is_quota_error(exc)
                    ):
                        reason = (
                            "capacity_overload"
                            if _is_transient_capacity_error(exc)
                            else "quota_rate_limit"
                        )
                        log.warning(
                            "gemini_try_next_model",
                            figure=figure_name,
                            reason=reason,
                            from_model=model_name,
                            to_model=models[idx + 1],
                        )
                        continue
                    if _is_quota_error(exc):
                        break
                    raise

            if last_exc is None:
                raise RuntimeError("planner_gemini: no model attempts (empty chain)")

            if _is_per_minute_limit(last_exc):
                wait_s = 65.0
                log.warning(
                    "gemini_rate_limit_per_minute",
                    figure=figure_name,
                    wait_seconds=int(wait_s),
                    note="Per-minute limit on all chain models — retrying in 65 s",
                )
            else:
                wait_s = _seconds_until_daily_reset() + _RESET_BUFFER_S
                resume_dt = datetime.datetime.now(tz=_PT) + datetime.timedelta(seconds=wait_s)
                h, rem = divmod(int(wait_s), 3600)
                m = rem // 60
                log.warning(
                    "gemini_daily_quota_hit",
                    figure=figure_name,
                    wait_hours=h,
                    wait_minutes=m,
                    resumes_at=resume_dt.strftime("%Y-%m-%d %H:%M PT"),
                    note=(
                        f"All Gemini models in chain hit daily limit — paused "
                        f"{h}h {m}m until quota resets at midnight PT"
                    ),
                )

            time.sleep(wait_s)
            log.info("gemini_quota_wait_done_retrying", figure=figure_name)


# ── Hybrid tiered failover client ─────────────────────────────────────────────

class _HybridClient:
    """
    Three-tier failover: Gemini primary → Gemini secondary → DeepSeek.

    Tier 0 (Primary)   : models[0]  e.g. gemini-2.5-flash
    Tier 1 (Secondary) : models[1+] e.g. gemini-3.1-flash-lite
    Tier 2 (Final)     : DeepSeek-V3 / R1

    Switching triggers
    ------------------
    503 or 429 on Tier N → try Tier N+1 immediately (no long backoff).
    All Gemini tiers fail → DeepSeek + SHORTS_HYBRID_GEMINI_COOLDOWN_S cooldown.
    Gemini in cooldown   → skip Gemini entirely, go straight to DeepSeek.
    Successful Gemini    → reset cooldown (primary fully restored).

    Exponential backoff is applied only if DeepSeek itself is also unavailable,
    preventing a thundering-herd of retries when every provider is down at once.
    """

    # If all providers fail, retry the whole cycle with this base delay (seconds).
    _ALL_FAIL_BACKOFF_BASE_S = 30.0
    _ALL_FAIL_BACKOFF_MAX_S = 300.0
    _ALL_FAIL_MAX_CYCLES = 4

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._health = _get_gemini_health(settings.hybrid_gemini_cooldown_s)

    def generate_plan(
        self,
        figure_name: str,
        *,
        topic_type: str,
        language: str,
        user_feedback: str | None = None,
    ) -> NarrationPlan:
        last_all_fail_exc: Exception | None = None

        for cycle in range(1, self._ALL_FAIL_MAX_CYCLES + 1):
            try:
                return self._attempt_cycle(
                    figure_name, topic_type=topic_type, language=language,
                    user_feedback=user_feedback,
                )
            except _AllProvidersFailed as apf:
                last_all_fail_exc = apf.cause
                if cycle >= self._ALL_FAIL_MAX_CYCLES:
                    break
                delay = min(
                    self._ALL_FAIL_BACKOFF_MAX_S,
                    self._ALL_FAIL_BACKOFF_BASE_S * (2 ** (cycle - 1)),
                )
                log.warning(
                    "planner_hybrid_all_providers_failed_backoff",
                    figure=figure_name,
                    cycle=cycle,
                    max_cycles=self._ALL_FAIL_MAX_CYCLES,
                    sleep_seconds=round(delay),
                    note=f"All providers failed — retrying in {round(delay)}s",
                )
                time.sleep(delay)

        raise RuntimeError(
            f"All LLM providers failed after {self._ALL_FAIL_MAX_CYCLES} cycles "
            f"for figure={figure_name!r}: {last_all_fail_exc}"
        ) from last_all_fail_exc

    def _attempt_cycle(
        self,
        figure_name: str,
        *,
        topic_type: str,
        language: str,
        user_feedback: str | None = None,
    ) -> NarrationPlan:
        """One attempt across all tiers.  Raises _AllProvidersFailed if every tier fails."""
        from shorts_pipeline.planner.gemini_client import GeminiPlannerClient

        models = self._settings.gemini_models_ordered()
        fast_fail_retries = getattr(
            self._settings, "hybrid_gemini_transient_retries", 2
        )

        # ── Check Gemini cooldown ──────────────────────────────────────────────
        if not self._health.is_healthy():
            remaining = self._health.remaining_s()
            log.warning(
                "planner_hybrid_gemini_cooldown_active",
                figure=figure_name,
                cooldown_remaining_s=round(remaining),
                switch_event=(
                    f"[SWITCH] Gemini in cooldown ({round(remaining)}s remaining)"
                    " — routing directly to DeepSeek"
                ),
            )
            return self._try_deepseek(
                figure_name, topic_type=topic_type, language=language,
                user_feedback=user_feedback,
            )
        else:
            # Log auto-recovery the first time we exit cooldown
            pass

        # ── Try each Gemini model (fast-fail on 503 / 429) ────────────────────
        last_gemini_exc: Exception | None = None
        for idx, model_name in enumerate(models):
            try:
                log.info(
                    "planner_hybrid_gemini_attempt",
                    figure=figure_name,
                    model=model_name,
                    tier=idx,
                )
                plan = GeminiPlannerClient(
                    self._settings,
                    model=model_name,
                    max_transient_http_retries=fast_fail_retries,
                ).generate_plan(
                    figure_name, topic_type=topic_type, language=language,
                    user_feedback=user_feedback,
                )

                # Success — cancel any cooldown and return
                self._health.mark_healthy()
                log.info(
                    "planner_hybrid_gemini_success",
                    figure=figure_name,
                    model=model_name,
                    tier=idx,
                    note="[RECOVER] Gemini healthy — primary restored",
                )
                return plan

            except Exception as exc:
                is_cap = _is_transient_capacity_error(exc)
                is_quota = _is_quota_error(exc)

                if not (is_cap or is_quota):
                    # Non-recoverable (auth error, validation bug, etc.) — propagate.
                    raise

                last_gemini_exc = exc
                reason = "503_capacity" if is_cap else "429_rate_limit"
                has_next_model = idx + 1 < len(models)

                if has_next_model:
                    next_model = models[idx + 1]
                    log.warning(
                        "planner_hybrid_gemini_switch_model",
                        figure=figure_name,
                        from_model=model_name,
                        to_model=next_model,
                        reason=reason,
                        switch_event=(
                            f"[SWITCH] {model_name} {reason}"
                            f" — routing to {next_model}"
                        ),
                    )
                    continue

                # All Gemini models exhausted
                log.warning(
                    "planner_hybrid_all_gemini_exhausted",
                    figure=figure_name,
                    reason=reason,
                    switch_event=(
                        f"[SWITCH] All Gemini models exhausted ({reason})"
                        " — routing to DeepSeek"
                    ),
                )

        # ── All Gemini models failed: mark degraded + route to DeepSeek ───────
        self._health.mark_degraded()
        cooldown = self._settings.hybrid_gemini_cooldown_s
        log.warning(
            "planner_hybrid_gemini_degraded",
            figure=figure_name,
            cooldown_s=cooldown,
            note=f"Gemini marked degraded for {cooldown}s — DeepSeek will handle next jobs too",
        )
        return self._try_deepseek(
            figure_name, topic_type=topic_type, language=language,
            user_feedback=user_feedback,
        )

    def _try_deepseek(
        self,
        figure_name: str,
        *,
        topic_type: str,
        language: str,
        user_feedback: str | None = None,
    ) -> NarrationPlan:
        from shorts_pipeline.planner.deepseek_client import DeepSeekPlannerClient, DeepSeekPlannerError

        if not self._settings.deepseek_api_key:
            raise ValueError(
                "All Gemini models failed but SHORTS_DEEPSEEK_API_KEY is not set. "
                "Add a DeepSeek key for hybrid fallback, or set "
                "SHORTS_PLANNER_BACKEND=gemini (pipeline waits for quota reset)."
            )

        try:
            return DeepSeekPlannerClient(self._settings).generate_plan(
                figure_name, topic_type=topic_type, language=language,
                user_feedback=user_feedback,
            )
        except DeepSeekPlannerError as exc:
            raise _AllProvidersFailed(exc) from exc


class _AllProvidersFailed(Exception):
    """Internal sentinel — raised when both Gemini and DeepSeek tiers fail."""

    def __init__(self, cause: Exception) -> None:
        super().__init__(str(cause))
        self.cause = cause


# ── Public factory ─────────────────────────────────────────────────────────────

def build_planner_client(settings: Settings) -> PlannerClient:
    """Return the correct planner client for settings.planner_backend."""
    backend = settings.planner_backend.lower().strip()

    if backend in ("gemini", "auto"):
        return _GeminiClient(settings)

    if backend == "hybrid":
        if not settings.gemini_api_key:
            raise ValueError(
                "planner_backend='hybrid' requires SHORTS_GEMINI_API_KEY "
                "(Gemini is tried first in every cycle)."
            )
        if not settings.deepseek_api_key:
            raise ValueError(
                "planner_backend='hybrid' requires SHORTS_DEEPSEEK_API_KEY "
                "for the DeepSeek final-tier fallback."
            )
        log.info(
            "planner_backend_hybrid",
            gemini_chain=settings.gemini_models_ordered(),
            deepseek_model=settings.deepseek_model,
            gemini_cooldown_s=settings.hybrid_gemini_cooldown_s,
        )
        return _HybridClient(settings)

    if backend == "ollama":
        raise ValueError(
            "planner_backend='ollama' is no longer supported. "
            "Use SHORTS_PLANNER_BACKEND=gemini or hybrid."
        )

    raise ValueError(
        f"Unknown planner_backend {backend!r}. Valid values: 'gemini', 'hybrid', 'auto'."
    )
