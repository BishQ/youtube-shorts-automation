"""Last-response snapshot for Gemini planner (rate-limit headers + token usage).

Google sometimes returns ``x-ratelimit-*`` (and related) headers on successful
``generateContent`` responses. When they are absent, the UI still shows token
counts from ``usage_metadata`` so you can sanity-check load.
"""

from __future__ import annotations

import threading
import time
from typing import Any

_lock = threading.Lock()
_last_unix: float = 0.0
_last: dict[str, Any] | None = None

# Lowercased header substrings treated as quota / rate-limit signals.
_RL_HINT = ("ratelimit", "x-goog-quota", "quota-", "retry-after")


def record_generate_content_response(resp: object) -> None:
    """Update snapshot after ``models.generate_content`` returns."""
    global _last, _last_unix
    headers_out: dict[str, str] = {}
    try:
        http = getattr(resp, "sdk_http_response", None)
        hdrs = getattr(http, "headers", None) if http is not None else None
        if isinstance(hdrs, dict):
            for key, val in hdrs.items():
                lk = key.lower()
                if any(h in lk for h in _RL_HINT):
                    headers_out[key] = val if isinstance(val, str) else str(val)
    except Exception:
        pass

    usage: dict[str, int | None] = {}
    try:
        um = getattr(resp, "usage_metadata", None)
        if um is not None:
            usage["prompt_token_count"] = getattr(um, "prompt_token_count", None)
            usage["candidates_token_count"] = getattr(um, "candidates_token_count", None)
            usage["total_token_count"] = getattr(um, "total_token_count", None)
    except Exception:
        pass

    snap: dict[str, Any] = {
        "rate_limit_headers": headers_out or None,
        "usage_metadata": usage or None,
        "model_version": getattr(resp, "model_version", None),
    }
    with _lock:
        _last = snap
        _last_unix = time.time()


def get_usage_snapshot() -> dict[str, Any]:
    with _lock:
        if _last is None:
            return {
                "has_sample": False,
                "updated_at_unix": None,
                "rate_limit_headers": None,
                "usage_metadata": None,
                "model_version": None,
            }
        return {
            "has_sample": True,
            "updated_at_unix": _last_unix,
            **_last,
        }
