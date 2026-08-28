"""Tests for the oversized-MCP-result key summary helper.

Covers the Q2 fix: ``_json_keys_summary`` extracts a compact field/key summary
from a JSON payload so the model can build a view→field mapping without READ-ing
the full materialized file (the "describe-loop thrash" root cause).
"""

from __future__ import annotations

import json

from app.services.mcp_client import _json_keys_summary


def test_dict_with_list_of_objects_surfaces_nested_fields():
    payload = json.dumps(
        {
            "views": [{"name": "v1", "fields": ["a"]}, {"name": "v2"}],
            "total": 2,
        }
    )
    s = _json_keys_summary(payload)
    assert "结构摘要" in s
    assert "views" in s
    assert "name" in s  # nested array element field surfaced


def test_list_of_objects_surfaces_fields_and_count():
    payload = json.dumps([{"name": "v1", "id": 1}, {"name": "v2", "id": 2}])
    s = _json_keys_summary(payload)
    assert "[2 项]" in s
    assert "name" in s
    assert "id" in s


def test_non_json_returns_empty_string():
    assert _json_keys_summary("not json at all") == ""


def test_empty_list_returns_empty():
    assert _json_keys_summary("[]") == ""
