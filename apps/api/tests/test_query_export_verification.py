import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from openpyxl import Workbook

from app.services.export_verifier import (
    diagnose_query_export_change,
    format_query_export_verification,
    inspect_query_deliverable,
    verify_query_export_delivery,
)


def _workbook(path: Path, rows: list[tuple]) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "数据"
    ws.append(["标识", "总额"])
    for row in rows:
        ws.append(row)
    wb.create_sheet("口径说明")
    wb.save(path)
    wb.close()


def test_query_export_verification_compares_runtime_count_file_and_prior(tmp_path):
    prior_path = tmp_path / "prior.xlsx"
    current_path = tmp_path / "current.xlsx"
    _workbook(prior_path, [(1, 10)])
    _workbook(current_path, [(1, 10), (2, 20)])

    result = verify_query_export_delivery(
        current=inspect_query_deliverable(current_path),
        requested_columns=["标识", "总额"],
        expected_row_count=2,
        fetched_row_count=2,
        pages_fetched=1,
        mcp_call_count=5,
        prior=inspect_query_deliverable(prior_path),
        verification_targets=["数据量"],
    )

    assert result["status"] == "pass"
    assert result["comparison"]["data_changed"] is True
    assert result["comparison"]["row_count_before"] == 1
    text = format_query_export_verification(result)
    assert "COUNT=2" in text
    assert "1 → 2" in text


def test_query_export_verification_fails_on_materialization_mismatch(tmp_path):
    path = tmp_path / "current.xlsx"
    _workbook(path, [(1, 10)])
    result = verify_query_export_delivery(
        current=inspect_query_deliverable(path),
        requested_columns=["标识", "总额"],
        expected_row_count=2,
        fetched_row_count=2,
        pages_fetched=1,
        mcp_call_count=4,
    )
    assert result["status"] == "failed"
    assert result["checks"]["fetch_matches_file"] is False


def test_change_diagnosis_uses_runtime_evidence_and_formats_concrete_cause():
    llm = SimpleNamespace(type="llm", model="x")
    response = json.dumps({
        "summary": "上一版额外过滤了零金额事件，当前版按事件类型统计。",
        "changes": [{
            "subject": "用户范围",
            "before": "event_type = 1 AND amount > 0",
            "after": "event_type = 1",
            "cause": "字段备注表明零金额事件仍属于目标行为，因此上一版会漏计。",
        }],
    }, ensure_ascii=False)
    mock = AsyncMock(return_value=response)
    verification = {
        "status": "pass",
        "expected_row_count": 980,
        "fetched_row_count": 980,
        "file_row_count": 980,
        "comparison": {
            "available": True,
            "row_count_before": 778,
            "row_count_after": 980,
            "data_changed": True,
        },
    }

    async def _run():
        with patch("app.services.llm_client.chat_completion", new=mock):
            return await diagnose_query_export_change(
                llm,
                prior_sql="SELECT uid FROM ads.events WHERE event_type=1 AND amount>0",
                current_sql="SELECT uid FROM ads.events WHERE event_type=1",
                schemas={"events": {"comments": {"event_type": "目标行为类型"}}},
                verification=verification,
                verification_targets=["人数差异"],
                user_context="为什么人数少了",
            )

    diagnosis = asyncio.run(_run())
    verification["diagnosis"] = diagnosis
    text = format_query_export_verification(verification)

    assert diagnosis["runtime_evidence"]["row_count_before"] == 778
    assert "### 差异原因" in text
    assert "amount > 0" in text
    assert "漏计" in text
    prompt_payload = json.loads(mock.await_args.args[1][1]["content"])
    assert prompt_payload["runtime_evidence"]["row_count_after"] == 980
