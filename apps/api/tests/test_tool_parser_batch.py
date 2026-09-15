from __future__ import annotations

from app.services.tool_parser import extract_tool_steps


def _batch(mode: str = "parallel") -> str:
    return (
        'BATCH: {"mode":"%s","children":['
        '{"id":"read-a","reply":"READ: a.py"},'
        '{"id":"grep-b","reply":"SEARCH: keyword"}'
        "]}") % mode


def test_batch_plain_protocol_accepts_parallel_sequence_transaction():
    for mode in ("parallel", "sequence", "transaction"):
        steps = extract_tool_steps(_batch(mode))

        assert len(steps) == 1
        assert steps[0].action == "tool_batch"
        assert steps[0].batch == {
            "mode": mode,
            "children": [
                {"id": "read-a", "action": "file_read", "reply": "READ: a.py"},
                {"id": "grep-b", "action": "file_search", "reply": "SEARCH: keyword"},
            ],
        }
        assert steps[0].reply.startswith("BATCH: ")


def test_batch_whole_json_tool_object_is_normalized():
    steps = extract_tool_steps(
        '{"name":"BATCH","arguments":{"mode":"parallel","children":['
        '{"id":"mcp-1","name":"MCP","arguments":{"tool_name":"list_notes","limit":5}}'
        ']}}'
    )

    assert len(steps) == 1
    assert steps[0].action == "tool_batch"
    assert steps[0].batch == {
        "mode": "parallel",
        "children": [
            {
                "id": "mcp-1",
                "action": "mcp_tool_call",
                "reply": 'MCP: list_notes {"limit": 5}',
            }
        ],
    }


def test_batch_preserves_explicit_batch_identifier():
    steps = extract_tool_steps(
        'BATCH: {"batch_id":"read-set-1","mode":"parallel","children":['
        '{"id":"r1","reply":"READ: a.py"}]}'
    )

    assert steps[0].batch["id"] == "read-set-1"


def test_batch_rejects_missing_mode_before_children_execute():
    steps = extract_tool_steps('BATCH: {"children":[{"id":"r1","reply":"READ: a.py"}]}')

    assert len(steps) == 1
    assert steps[0].action == "tool_batch_invalid"
    assert "missing_or_invalid_mode" in steps[0].reply


def test_batch_rejects_duplicate_child_ids():
    steps = extract_tool_steps(
        'BATCH: {"mode":"parallel","children":['
        '{"id":"same","reply":"READ: a.py"},'
        '{"id":"same","reply":"READ: b.py"}'
        "]}",
    )

    assert len(steps) == 1
    assert steps[0].action == "tool_batch_invalid"
    assert "duplicate_child_id:same" in steps[0].reply


def test_batch_rejects_malformed_child_without_tool_identity():
    steps = extract_tool_steps(
        'BATCH: {"mode":"parallel","children":[{"id":"bad","arguments":{"x":1}}]}'
    )

    assert len(steps) == 1
    assert steps[0].action == "tool_batch_invalid"
    assert "child_missing_action:bad" in steps[0].reply
