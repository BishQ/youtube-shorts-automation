"""LM Studio helpers — free GPU VRAM after the plan stage."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import httpx

from shorts_pipeline.config.settings import Settings
from shorts_pipeline.logging_setup import get_logger

log = get_logger(__name__)


def _api_root(base_url: str) -> str:
    u = base_url.rstrip("/")
    if u.endswith("/v1"):
        return u[:-3]
    return u


def _loaded_instance_ids(data: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for entry in data.get("models") or []:
        if not isinstance(entry, dict):
            continue
        for inst in entry.get("loaded_instances") or []:
            if isinstance(inst, dict):
                iid = inst.get("id")
                if isinstance(iid, str) and iid:
                    ids.append(iid)
    return ids


def unload_lm_studio_models(settings: Settings) -> bool:
    """Unload all LM Studio model instances so ComfyUI can use the GPU.

    Best-effort: logs and returns False on failure without raising.
    """
    root = _api_root(settings.local_llm_base_url)
    list_url = f"{root}/api/v1/models"
    unload_url = f"{root}/api/v1/models/unload"
    timeout = min(settings.local_llm_timeout_s, 60.0)

    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.get(list_url)
            r.raise_for_status()
            payload = r.json()
            if not isinstance(payload, dict):
                log.warning("lm_studio_unload_unexpected_list_response")
                return False
            instance_ids = _loaded_instance_ids(payload)
            if not instance_ids and settings.local_llm_model:
                instance_ids = [settings.local_llm_model]
            if not instance_ids:
                log.info("lm_studio_unload_nothing_loaded")
                return True
            for iid in instance_ids:
                ur = client.post(unload_url, json={"instance_id": iid})
                ur.raise_for_status()
                log.info("lm_studio_model_unloaded", instance_id=iid)
        return True
    except Exception as e:
        host = urlparse(root).netloc or root
        log.warning(
            "lm_studio_unload_failed",
            host=host,
            error=str(e),
            hint="Unload the model manually in LM Studio (Developer -> Local Server).",
        )
        return False
