"""Comfy queue batch polling."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from shorts_pipeline.config.settings import Settings
from shorts_pipeline.image_worker.comfy import ComfyClient, ComfyError


def test_wait_for_prompts_collects_all_histories(tmp_path: Path) -> None:
    s = Settings(data_dir=tmp_path, comfy_poll_interval_s=0.01, comfy_max_polls=5)
    client = ComfyClient(s)
    calls = {"a": 0, "b": 0}

    def fake_history(pid: str):
        calls[pid] = calls.get(pid, 0) + 1
        if calls[pid] >= 2:
            return {"outputs": {"1": {"images": [{"filename": "x.png"}]}}}
        return None

    with patch.object(client, "get_history", side_effect=fake_history):
        with patch.object(client, "_request", return_value=MagicMock(status_code=200)):
            out = client.wait_for_prompts(["a", "b"])

    assert set(out) == {"a", "b"}
    assert all(h.get("outputs") for h in out.values())


def test_wait_for_prompts_raises_on_timeout(tmp_path: Path) -> None:
    s = Settings(data_dir=tmp_path, comfy_poll_interval_s=0.01, comfy_max_polls=2)
    client = ComfyClient(s)

    with patch.object(client, "get_history", return_value=None):
        with patch.object(client, "_request", return_value=MagicMock(status_code=200)):
            with pytest.raises(ComfyError, match="did not complete"):
                client.wait_for_prompts(["slow-id"])
