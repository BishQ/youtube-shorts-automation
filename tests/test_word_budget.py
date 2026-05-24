"""Unit tests for ``planner.word_budget.maybe_clamp_plan_json``."""

from __future__ import annotations

from shorts_pipeline.planner.word_budget import maybe_clamp_plan_json


def _make_clause(num_words: int) -> dict:
    return {"text": " ".join(f"w{i}" for i in range(num_words)), "image_prompt": "x" * 50}


def test_clamp_trims_oversized_script() -> None:
    lengths = [23, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 21]  # totals 200
    clauses = [_make_clause(n) for n in lengths]
    clauses[0]["text"] = (
        "How does a shy child become a storm the world still whispers about? "
        + " ".join(f"t{i}" for i in range(10))  # 10 tail tokens after the hook sentence
    )

    full_script = " ".join(c["text"] for c in clauses)
    assert len(full_script.split()) == 200

    obj: dict = {
        "historical_figure": "Test Subject",
        "cold_open_object": "ancient coin macro",
        "decision_lever": {
            "lever_type": "politics",
            "description": "A decisive political lever that split old allies",
            "consequence": "Trade routes collapsed and the palace lost its tax base overnight",
        },
        "clauses": clauses,
        "full_script": full_script,
        "end_plate_question": "What would YOU have done?",
    }

    changed = maybe_clamp_plan_json(obj, max_words=185)
    assert changed is True

    final_wc = len(obj["full_script"].split())
    assert final_wc <= 185
    assert len(obj["clauses"]) == 14
    hook = obj["clauses"][0]["text"]
    assert "?" in hook


def test_resync_when_full_script_bulkier_than_clauses() -> None:
    clauses = [_make_clause(10) for _ in range(14)]
    join_wc = sum(10 for _ in range(14))
    assert join_wc == 140

    obj: dict = {
        "clauses": clauses,
        "full_script": " ".join(["extra"] * 200),  # garbage longer field
    }

    changed = maybe_clamp_plan_json(obj, max_words=185)
    assert changed is True
    assert len(obj["full_script"].split()) == join_wc


def test_default_clamp_uses_schema_max_words() -> None:
    lengths = [23, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 13, 46]
    clauses = [_make_clause(n) for n in lengths]
    clauses[0]["text"] = (
        "How does a shy child become a storm the world still whispers about? "
        + " ".join(f"t{i}" for i in range(10))
    )
    full_script = " ".join(c["text"] for c in clauses)
    assert len(full_script.split()) == 225

    obj: dict = {
        "historical_figure": "Test Subject",
        "cold_open_object": "ancient coin macro",
        "decision_lever": {
            "lever_type": "politics",
            "description": "A decisive political lever that split old allies",
            "consequence": "Trade routes collapsed and the palace lost its tax base overnight",
        },
        "clauses": clauses,
        "full_script": full_script,
        "end_plate_question": "What would YOU have done?",
    }

    assert maybe_clamp_plan_json(obj) is True
    final_wc = len(obj["full_script"].split())
    assert final_wc <= 210
    assert "?" in obj["clauses"][0]["text"]


    clauses = [_make_clause(8) for _ in range(14)]
    fs = " ".join(c["text"] for c in clauses)
    obj = {"clauses": clauses, "full_script": fs}
    assert maybe_clamp_plan_json(obj, max_words=185) is False
