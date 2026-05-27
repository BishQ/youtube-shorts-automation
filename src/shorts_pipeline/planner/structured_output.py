"""vLLM/OpenAI structured-output payload helpers."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


def json_schema_for(model: type[BaseModel], *, name: str) -> dict[str, Any]:
    """Return a compact JSON schema suitable for vLLM guided decoding."""
    schema = model.model_json_schema()
    schema.setdefault("title", name)
    return schema


def apply_structured_output(
    payload: dict[str, Any],
    *,
    mode: str | None,
    schema: dict[str, Any],
    name: str,
) -> dict[str, Any]:
    """Attach the requested structured-output parameters to a chat payload."""
    selected = (mode or "guided_json").strip().lower()
    if selected in ("", "off", "none", "false", "0"):
        return payload
    out = dict(payload)
    if selected == "guided_json":
        out["guided_json"] = schema
        return out
    if selected == "json_schema":
        out["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": name,
                "schema": schema,
                "strict": True,
            },
        }
        return out
    if selected == "json_object":
        out["response_format"] = {"type": "json_object"}
        return out
    raise ValueError(
        "local_llm_structured_output must be one of: guided_json, json_schema, "
        "json_object, off"
    )


def structured_output_fallback_modes(primary: str) -> list[str | None]:
    """Modes to try in order; ``None`` = plain chat (no structured-output fields)."""
    selected = (primary or "guided_json").strip().lower()
    if selected == "guided_json":
        return ["guided_json", "json_schema", None]
    if selected == "json_object":
        return ["json_object", "json_schema", None]
    if selected == "json_schema":
        return ["json_schema", None]
    if selected in ("", "off", "none", "false", "0"):
        return [None]
    return [selected, "json_schema", None]


def looks_like_structured_output_rejection(detail: object) -> bool:
    text = str(detail).lower()
    return any(
        marker in text
        for marker in (
            "guided_json",
            "guided decoding",
            "response_format",
            "json_schema",
            "extra inputs are not permitted",
            "unrecognized request argument",
        )
    )
