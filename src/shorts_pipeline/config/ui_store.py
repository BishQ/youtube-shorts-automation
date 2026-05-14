"""Persist non-secret UI overrides (JSON file under data dir)."""

from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from typing import Any

from pydantic import BaseModel, ConfigDict

from shorts_pipeline.config.settings import Settings, get_settings


class UiOverrides(BaseModel):
    model_config = ConfigDict(extra="ignore")

    comfy_workflow_name: str | None = None
    whisper_model_size: str | None = None


class UiConfigStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> UiOverrides:
        with self._lock:
            if not self._path.is_file():
                return UiOverrides()
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return UiOverrides.model_validate(data)

    def save_patch(self, patch: dict[str, Any]) -> UiOverrides:
        cur = self.load().model_dump()
        cur.update({k: v for k, v in patch.items() if k in UiOverrides.model_fields})
        m = UiOverrides.model_validate(cur)
        with self._lock:
            self._path.write_text(m.model_dump_json(indent=2), encoding="utf-8")
        return m


_ui: UiConfigStore | None = None


def get_ui_store() -> UiConfigStore:
    global _ui
    if _ui is None:
        s = get_settings()
        _ui = UiConfigStore(s.data_dir / "ui_config.json")
    return _ui


def effective_settings(base: Settings) -> Settings:
    """Merge env settings with UI overrides (non-secrets only)."""
    ui = get_ui_store().load()
    return base.model_copy(
        update={
            "comfy_workflow_name": ui.comfy_workflow_name or base.comfy_workflow_name,
            "whisper_model_size": ui.whisper_model_size or base.whisper_model_size,
        }
    )
