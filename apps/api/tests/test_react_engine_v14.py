"""react-engine-v14 tests (carried-over R3 only).

v14 R1 (无进展软预算提前收尾) and R2 (小任务跳过完成度复核) were reverted in
react-engine-v15 — their replacement tests live in ``test_react_engine_v15.py``.
R3 (数据视图目录缓存与注入) is carried over unchanged, so its tests stay here.
"""

from __future__ import annotations

from app.services.agent_runtime.context_manager import ContextManager
from app.services.agent_runtime.utils import (
    build_ads_view_catalog,
    get_ads_views_cached,
    record_ads_view_catalog,
)


# ---- R3: 数据视图目录缓存与注入 ----

def test_record_and_build_ads_view_catalog():
    record_ads_view_catalog(
        "mcp-A", "list_ads_views", {},
        '[{"view": "ads_orders"}, {"view": "ads_users"}]',
    )
    record_ads_view_catalog(
        "mcp-A", "describe_ads_view", {"view": "ads_orders"},
        '{"order_id": 1, "amount": 2, "dt": 3}',
    )

    names, mapping = get_ads_views_cached(["mcp-A"])
    assert names == ["ads_orders", "ads_users"]
    assert "ads_orders" in mapping

    catalog = build_ads_view_catalog(["mcp-A"])
    assert "可用视图名" in catalog
    assert "ads_orders" in catalog
    assert "已确认字段口径" in catalog


def test_ads_view_catalog_empty_when_no_cache():
    assert build_ads_view_catalog(["mcp-none"]) == ""


def test_ads_view_catalog_error_result_not_cached():
    record_ads_view_catalog("mcp-B", "list_ads_views", {}, "未在绑定 MCP 中找到工具 x")
    assert get_ads_views_cached(["mcp-B"]) == ([], {})


def test_task_context_injects_view_catalog():
    cm = ContextManager()
    cm.set_task_context(
        goal="导出报表",
        plan="- [ ] 定位视图",
        view_catalog="可用视图名：\n- ads_orders",
    )
    layer = cm._layers["task_context"]
    content = cm._messages[layer.start]["content"]
    assert "【任务目标】" in content
    assert "【当前计划】" in content
    assert "【数据视图目录】" in content
    assert "ads_orders" in content


def test_task_context_omits_view_catalog_when_absent():
    cm = ContextManager()
    cm.set_task_context(goal="导出报表")
    layer = cm._layers["task_context"]
    content = cm._messages[layer.start]["content"]
    assert "【数据视图目录】" not in content
