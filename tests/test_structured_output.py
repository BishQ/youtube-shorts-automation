from shorts_pipeline.config.settings import Settings
from shorts_pipeline.planner.client import _extract_json
from shorts_pipeline.planner.schema import NarrationPlan
from shorts_pipeline.planner.structured_output import apply_structured_output, json_schema_for
from shorts_pipeline.planner.vllm_client import VllmPlannerClient, VllmPlannerError


def test_apply_structured_output_adds_vllm_guided_json() -> None:
    schema = json_schema_for(NarrationPlan, name="NarrationPlan")
    payload = apply_structured_output(
        {"model": "m", "messages": []},
        mode="guided_json",
        schema=schema,
        name="NarrationPlan",
    )

    assert payload["guided_json"]["title"] == "NarrationPlan"
    assert "properties" in payload["guided_json"]


def test_vllm_planner_structured_fallback_to_json_object(monkeypatch) -> None:
    settings = Settings.model_construct(
        local_llm_model="test-model",
        local_llm_base_url="http://127.0.0.1:8000/v1",
        local_llm_timeout_s=1.0,
        local_llm_structured_output="guided_json",
    )
    client = VllmPlannerClient(settings)
    seen: list[dict] = []

    def fake_post(payload: dict):
        seen.append(payload)
        if len(seen) == 1:
            raise VllmPlannerError("bad request", status_code=400, detail="guided_json unsupported")
        return '{"ok": true}'

    monkeypatch.setattr(client, "_post", fake_post)

    out = client._post_structured({"model": "m", "messages": []}, schema={"type": "object"})

    assert out == '{"ok": true}'
    assert "guided_json" in seen[0]
    assert seen[1]["response_format"]["type"] == "json_schema"


def test_extract_json_repairs_js_style_object() -> None:
    obj = _extract_json("{name: 'David Bowie', ok: True, count: 2,}")

    assert obj == {"name": "David Bowie", "ok": True, "count": 2}


def test_extract_json_uses_first_balanced_object_with_extra_data() -> None:
    obj = _extract_json('{"name": "David Bowie"} {"ignored": true}')

    assert obj == {"name": "David Bowie"}
