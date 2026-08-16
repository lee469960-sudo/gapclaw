import json

from app.services.react_engine import _temporal_list_contract_annotation


def test_list_result_annotation_uses_created_at_half_open_window():
    result = json.dumps({
        "items": [
            {"id": "next", "title": "11号", "created_at": "2026-08-11T00:01:00+08:00"},
            {"id": "a", "title": "10号早", "created_at": "2026-08-10T08:00:00+08:00"},
            {"id": "b", "title": "10号晚", "created_at": "2026-08-10T23:59:59+08:00"},
        ],
        "has_more": True,
        "cursor": "c1",
    }, ensure_ascii=False)
    note = _temporal_list_contract_annotation(
        "list_notes",
        result,
        {"start_ms": 1786291200000, "end_ms": 1786377600000},
    )

    assert "命中 2 条" in note
    assert "a（10号早）" in note
    assert "b（10号晚）" in note
    assert "next（11号）" not in note
    assert "继续翻页" in note


def test_non_list_tool_is_not_temporally_annotated():
    assert _temporal_list_contract_annotation(
        "get_note",
        '[{"id":"a","created_at":1786291200000}]',
        {"start_ms": 1786291200000, "end_ms": 1786377600000},
    ) == ""
