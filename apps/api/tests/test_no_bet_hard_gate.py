"""No hard gate that expands empty targets to pay/cash/bet (esp. bet)."""

from __future__ import annotations

from app.services.react_engine import _format_resource_binding_gap
from app.services.skill_lesson import infer_repair_gaps


def test_infer_repair_gaps_empty_target_no_bet():
    gaps = infer_repair_gaps(
        {
            "target_roles": [],
            "missing_roles": [],
            "covered_roles": ["user"],
            "fetched_view_pages": {"view_result_user_info": 2},
        }
    )
    assert "bet" not in gaps
    assert "pay" not in gaps
    assert "cash" not in gaps


def test_infer_repair_gaps_pay_only_target_excludes_bet():
    gaps = infer_repair_gaps(
        {
            "target_roles": ["user", "pay"],
            "missing_roles": [],
            "covered_roles": ["user"],
            "fetched_view_pages": {
                "view_result_user_info": 1,
                # pay and bet both 0 pages — only pay is targeted
            },
        }
    )
    assert "pay" in gaps
    assert "bet" not in gaps
    assert "cash" not in gaps


def test_resource_binding_gap_never_invents_a_default_source():
    text = _format_resource_binding_gap(["pay"])
    assert "资源绑定缺口" in text
    assert "pay" in text
    assert "默认视图" in text
    assert "MCP list/describe" in text
