"""Publisher router — Gemini chain + DeepSeek hybrid failover.

Reuses the planner's process-wide Gemini health tracker so both planner and
publisher respect the same cooldown window. When the planner has just been
forced onto DeepSeek, the publisher will skip Gemini too for the same job —
this is correct behaviour because both run in the same pipeline tick and
share the same upstream quota state.

Backend selection mirrors `settings.planner_backend`:
  • "gemini"  → Gemini-only, waits for daily quota reset.
  • "hybrid"  → Gemini chain → DeepSeek fallback with cooldown.
  • "auto"    → alias for "gemini".
"""

from __future__ import annotations

import datetime
import time
from typing import Protocol, runtime_checkable
from zoneinfo import ZoneInfo

from shorts_pipeline.config.settings import Settings
from shorts_pipeline.logging_setup import get_logger
from shorts_pipeline.planner.router import (
    _AllProvidersFailed,
    _get_gemini_health,
    _is_per_minute_limit,
    _is_quota_error,
    _is_transient_capacity_error,
)
from shorts_pipeline.planner.schema import NarrationPlan
from shorts_pipeline.publisher.schema import PublishingPackage

log = get_logger(__name__)

_PT = ZoneInfo("America/Los_Angeles")
_RESET_BUFFER_S = 90


def _seconds_until_daily_reset() -> float:
    now_pt = datetime.datetime.now(tz=_PT)
    next_midnight = (now_pt + datetime.timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return (next_midnight - now_pt).total_seconds()


# ── Protocol ──────────────────────────────────────────────────────────────────

@runtime_checkable
class PublisherClient(Protocol):
    def generate_package(
        self,
        figure_name: str,
        plan: NarrationPlan,
    ) -> PublishingPackage: ...


# ── Pure-Gemini client (waits for daily quota reset) ──────────────────────────

class _GeminiOnlyClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def generate_package(
        self,
        figure_name: str,
        plan: NarrationPlan,
    ) -> PublishingPackage:
        from shorts_pipeline.publisher.gemini_client import GeminiPublisherClient

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
                        "publisher_gemini_attempt",
                        figure=figure_name,
                        model=model_name,
                        model_index=idx,
                    )
                    return GeminiPublisherClient(
                        self._settings, model=model_name
                    ).generate_package(figure_name, plan)
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
                            "publisher_gemini_try_next_model",
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
                raise RuntimeError("publisher_gemini: no model attempts (empty chain)")

            if _is_per_minute_limit(last_exc):
                wait_s = 65.0
                log.warning(
                    "publisher_gemini_rate_limit_per_minute",
                    figure=figure_name,
                    wait_seconds=int(wait_s),
                )
            else:
                wait_s = _seconds_until_daily_reset() + _RESET_BUFFER_S
                resume_dt = datetime.datetime.now(tz=_PT) + datetime.timedelta(seconds=wait_s)
                h, rem = divmod(int(wait_s), 3600)
                m = rem // 60
                log.warning(
                    "publisher_gemini_daily_quota_hit",
                    figure=figure_name,
                    wait_hours=h,
                    wait_minutes=m,
                    resumes_at=resume_dt.strftime("%Y-%m-%d %H:%M PT"),
                )

            time.sleep(wait_s)
            log.info("publisher_gemini_quota_wait_done_retrying", figure=figure_name)


# ── Hybrid tiered failover ────────────────────────────────────────────────────

class _HybridClient:
    """Gemini primary → Gemini secondary → DeepSeek, with bounded retry cycles."""

    _ALL_FAIL_BACKOFF_BASE_S = 30.0
    _ALL_FAIL_BACKOFF_MAX_S = 300.0
    _ALL_FAIL_MAX_CYCLES = 3

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        # Share the planner's cooldown state — same Gemini API key/process.
        self._health = _get_gemini_health(settings.hybrid_gemini_cooldown_s)

    def generate_package(
        self,
        figure_name: str,
        plan: NarrationPlan,
    ) -> PublishingPackage:
        last_all_fail_exc: Exception | None = None

        for cycle in range(1, self._ALL_FAIL_MAX_CYCLES + 1):
            try:
                return self._attempt_cycle(figure_name, plan)
            except _AllProvidersFailed as apf:
                last_all_fail_exc = apf.cause
                if cycle >= self._ALL_FAIL_MAX_CYCLES:
                    break
                delay = min(
                    self._ALL_FAIL_BACKOFF_MAX_S,
                    self._ALL_FAIL_BACKOFF_BASE_S * (2 ** (cycle - 1)),
                )
                log.warning(
                    "publisher_hybrid_all_providers_failed_backoff",
                    figure=figure_name,
                    cycle=cycle,
                    max_cycles=self._ALL_FAIL_MAX_CYCLES,
                    sleep_seconds=round(delay),
                )
                time.sleep(delay)

        raise RuntimeError(
            f"All publisher LLM providers failed after {self._ALL_FAIL_MAX_CYCLES} cycles "
            f"for figure={figure_name!r}: {last_all_fail_exc}"
        ) from last_all_fail_exc

    def _attempt_cycle(
        self,
        figure_name: str,
        plan: NarrationPlan,
    ) -> PublishingPackage:
        from shorts_pipeline.publisher.gemini_client import GeminiPublisherClient

        models = self._settings.gemini_models_ordered()
        fast_fail_retries = getattr(
            self._settings, "hybrid_gemini_transient_retries", 2
        )

        if not self._health.is_healthy():
            remaining = self._health.remaining_s()
            log.warning(
                "publisher_hybrid_gemini_cooldown_active",
                figure=figure_name,
                cooldown_remaining_s=round(remaining),
                switch_event=(
                    f"[SWITCH] Publisher: Gemini in cooldown ({round(remaining)}s remaining)"
                    " — routing directly to DeepSeek"
                ),
            )
            return self._try_deepseek(figure_name, plan)

        last_gemini_exc: Exception | None = None
        for idx, model_name in enumerate(models):
            try:
                log.info(
                    "publisher_hybrid_gemini_attempt",
                    figure=figure_name,
                    model=model_name,
                    tier=idx,
                )
                pkg = GeminiPublisherClient(
                    self._settings,
                    model=model_name,
                    max_transient_http_retries=fast_fail_retries,
                ).generate_package(figure_name, plan)

                self._health.mark_healthy()
                log.info(
                    "publisher_hybrid_gemini_success",
                    figure=figure_name,
                    model=model_name,
                    tier=idx,
                )
                return pkg

            except Exception as exc:
                is_cap = _is_transient_capacity_error(exc)
                is_quota = _is_quota_error(exc)

                if not (is_cap or is_quota):
                    raise

                last_gemini_exc = exc
                reason = "503_capacity" if is_cap else "429_rate_limit"
                has_next_model = idx + 1 < len(models)

                if has_next_model:
                    next_model = models[idx + 1]
                    log.warning(
                        "publisher_hybrid_gemini_switch_model",
                        figure=figure_name,
                        from_model=model_name,
                        to_model=next_model,
                        reason=reason,
                        switch_event=(
                            f"[SWITCH] Publisher: {model_name} {reason}"
                            f" — routing to {next_model}"
                        ),
                    )
                    continue

                log.warning(
                    "publisher_hybrid_all_gemini_exhausted",
                    figure=figure_name,
                    reason=reason,
                    switch_event=(
                        f"[SWITCH] Publisher: All Gemini models exhausted ({reason})"
                        " — routing to DeepSeek"
                    ),
                )

        self._health.mark_degraded()
        cooldown = self._settings.hybrid_gemini_cooldown_s
        log.warning(
            "publisher_hybrid_gemini_degraded",
            figure=figure_name,
            cooldown_s=cooldown,
            last_error=str(last_gemini_exc)[:240] if last_gemini_exc else None,
        )
        return self._try_deepseek(figure_name, plan)

    def _try_deepseek(
        self,
        figure_name: str,
        plan: NarrationPlan,
    ) -> PublishingPackage:
        from shorts_pipeline.publisher.deepseek_client import (
            DeepSeekPublisherClient,
            DeepSeekPublisherError,
        )

        if not self._settings.deepseek_api_key:
            raise ValueError(
                "All Gemini models failed but SHORTS_DEEPSEEK_API_KEY is not set. "
                "Add a DeepSeek key for hybrid fallback, or set "
                "SHORTS_PLANNER_BACKEND=gemini (publisher waits for quota reset)."
            )

        try:
            return DeepSeekPublisherClient(self._settings).generate_package(
                figure_name, plan
            )
        except DeepSeekPublisherError as exc:
            raise _AllProvidersFailed(exc) from exc


# ── Public factory ────────────────────────────────────────────────────────────

def build_publisher_client(settings: Settings) -> PublisherClient:
    """Return the publisher client implied by settings.planner_backend."""
    backend = settings.planner_backend.lower().strip()

    if backend in ("gemini", "auto"):
        return _GeminiOnlyClient(settings)

    if backend == "hybrid":
        if not settings.gemini_api_key:
            raise ValueError(
                "planner_backend='hybrid' requires SHORTS_GEMINI_API_KEY for the publisher."
            )
        if not settings.deepseek_api_key:
            raise ValueError(
                "planner_backend='hybrid' requires SHORTS_DEEPSEEK_API_KEY "
                "for the publisher's DeepSeek final-tier fallback."
            )
        log.info(
            "publisher_backend_hybrid",
            gemini_chain=settings.gemini_models_ordered(),
            deepseek_model=settings.deepseek_model,
        )
        return _HybridClient(settings)

    if backend == "ollama":
        raise ValueError(
            "planner_backend='ollama' is not supported. Use 'gemini' or 'hybrid'."
        )

    raise ValueError(
        f"Unknown planner_backend {backend!r}. Valid values: 'gemini', 'hybrid', 'auto'."
    )
