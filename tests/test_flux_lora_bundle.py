"""Tests for Flux 2 LoRA workflow bundle loader."""

from pathlib import Path

import pytest

from shorts_pipeline.image_worker.flux_lora_generator import load_flux_lora_bundle

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / "workflows" / "flux2_dev_lora_full.json"


def test_load_flux_lora_bundle_meta() -> None:
    if not WF.is_file():
        pytest.skip("workflow not present")
    b = load_flux_lora_bundle(WF)
    assert b.prompt_key_path == ["5", "inputs", "text"]
    assert len(b.reference_image_key_paths) == 4
    assert b.lora_key_path == ["2", "inputs", "lora_name"]
