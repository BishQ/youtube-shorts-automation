"""Tests for Flux 2 identity workflow bundle loader."""

from pathlib import Path

import pytest

from shorts_pipeline.image_worker.flux_identity_generator import (
    _FluxIdentityCapable,
    load_flux_identity_bundle,
)
from shorts_pipeline.config.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / "workflows" / "flux2_dev_identity_full.json"


def test_load_flux_identity_bundle_meta() -> None:
    if not WF.is_file():
        pytest.skip("workflow not present")
    b = load_flux_identity_bundle(WF)
    assert b.prompt_key_path == ["5", "inputs", "text"]
    assert len(b.reference_image_key_paths) == 4
    assert b.pulid_model_key_path == ["32", "inputs", "pulid_file"]
    assert b.controlnet_name_key_path == ["50", "inputs", "controlnet_name"]
    assert b.face_mask_key_path == ["61", "inputs", "image"]


def test_patch_identity_workflow_sets_refs_and_mask() -> None:
    if not WF.is_file():
        pytest.skip("workflow not present")
    bundle = load_flux_identity_bundle(WF)
    cap = _FluxIdentityCapable()
    settings = Settings()
    wf = cap._patch_identity_workflow(
        bundle,
        prompt_text="portrait of a leader, cinematic",
        ref_uploaded_names=["a.png", "b.png", "c.png", "d.png"],
        face_mask_name="mask.png",
        settings=settings,
        seed=42,
    )
    assert wf["5"]["inputs"]["text"] == "portrait of a leader, cinematic"
    assert wf["11"]["inputs"]["image"] == "a.png"
    assert wf["14"]["inputs"]["image"] == "d.png"
    assert wf["61"]["inputs"]["image"] == "mask.png"
    assert wf["32"]["inputs"]["pulid_file"] == settings.flux_pulid_model_name
    assert wf["50"]["inputs"]["controlnet_name"] == settings.flux_controlnet_name
