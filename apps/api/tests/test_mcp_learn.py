"""MCP error class circuit + lesson / inject learning hooks."""

from pathlib import Path

from app.services.react_engine import (
    _classify_mcp_error,
    _is_mcp_preflight_feedback,
    _is_mcp_tool_failure,
    _step_results_are_mcp_failures_only,
    _summarize_mcp_failures,
)
from app.services.skill_lesson import (
    build_export_skill_lesson,
    gap_tags_for_rolling,
    load_recent_mcp_lesson_hints,
)


def test_classify_mcp_error_classes():
    assert _classify_mcp_error("MCP 错误: invalid_union") == "invalid_union"
    assert _classify_mcp_error("MCP 本地拦截: where 必须是 JSON 对象") == "where_sql"
    assert _classify_mcp_error("MCP 参数软提示: where 必须是 JSON 对象") == "where_sql"
    assert _classify_mcp_error("only select queries are allowed") == "select_only"
    assert _classify_mcp_error("unknown tool foo") == "unknown_tool"
    assert _classify_mcp_error("MCP 无响应") == "transport"
    assert _classify_mcp_error("MCP 本地拦截: 缺少 view") == "local_validate"
    assert _classify_mcp_error("MCP 参数软提示: 缺少 view") == "local_validate"


def test_is_mcp_tool_failure_broadened():
    assert _is_mcp_tool_failure("MCP 本地拦截: x")
    assert _is_mcp_tool_failure("MCP 参数软提示: x")
    assert _is_mcp_tool_failure("MCP URL 未配置")
    assert _is_mcp_tool_failure("MCP 无响应")
    assert _is_mcp_tool_failure("【硬熔断】`query_ads_view` 同类错误")
    assert not _is_mcp_tool_failure('{"rows":[]}')


def test_summarize_and_step_failures_only():
    summary = _summarize_mcp_failures(
        {"invalid_union": 4, "other": 1},
        {"invalid_union": "bad where", "other": "x"},
        {"invalid_union": "query_ads_view", "other": "list"},
    )
    assert summary[0]["class"] == "invalid_union"
    assert summary[0]["count"] == 4
    assert _is_mcp_preflight_feedback("MCP 本地拦截: where 必须")
    assert _step_results_are_mcp_failures_only([
        "[mcp_tool_call] MCP 错误: invalid_union",
        "[httpmcp_call] MCP 无响应",
    ])
    assert not _step_results_are_mcp_failures_only([
        "[mcp_tool_call] MCP 错误: x",
        "[shell] ok",
    ])
    assert not _step_results_are_mcp_failures_only([
        "[mcp_tool_call] MCP 错误: invalid_union",
        "[mcp_tool_call] MCP 本地拦截: where 必须",
    ])


def test_lesson_includes_mcp_failures():
    md = build_export_skill_lesson(
        run_id="mcp1",
        mode="analyzed",
        covered_roles=["user"],
        missing_roles=[],
        mcp_failures=[
            {
                "tool": "query_ads_view",
                "class": "invalid_union",
                "count": 3,
                "sample": "invalid_union where",
            }
        ],
    )
    assert "mcp_repeat:invalid_union" in md
    assert "### MCP 失败摘要" in md
    assert "MCP 反例" in md
    assert "where 必须是 JSON 对象" in md


def test_gap_tags_include_mcp():
    tags = gap_tags_for_rolling(
        missing_roles=[],
        fallback=False,
        mcp_failures=[{"class": "invalid_union", "count": 3}],
    )
    assert "[MCP:invalid_union]" in tags


def test_load_recent_mcp_lesson_hints_truncates(tmp_path, monkeypatch):
    sid = "sbx_mcp_hint"
    wp = tmp_path / sid
    lessons = wp / "lessons"
    lessons.mkdir(parents=True)
    monkeypatch.setattr(
        "app.services.skill_lesson.ensure_workplace",
        lambda s: wp if s == sid else Path("/nope"),
    )
    (lessons / "export_1.md").write_text(
        "## x\n\n### 根因候选（规则生成）\n"
        "- mcp_repeat:invalid_union：`query_ads_view` 同类失败 3 次\n\n"
        "### 建议写入 Skill（可复制）\n\n```markdown\n"
        "#### MCP 反例\n"
        "- `invalid_union`（query_ads_view ×3）：where 必须是 JSON 对象\n"
        "```\n",
        encoding="utf-8",
    )
    hint = load_recent_mcp_lesson_hints(sid, max_chars=120)
    assert hint.startswith("【近期 MCP 反例】")
    assert "invalid_union" in hint
    assert len(hint) <= 120
