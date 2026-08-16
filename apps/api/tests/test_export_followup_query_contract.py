import json
from types import SimpleNamespace

from app.services.react_engine import _find_recent_assistant_query_contract
from app.services.react_engine import _resolve_authoritative_query_contract


def test_followup_recovers_sql_and_columns_from_assistant_metadata():
    contract = {
        "sql": 'SELECT a.id AS "标识" FROM ads.live_alpha AS a',
        "output_columns": ["标识"],
        "resources": ["live_alpha"],
    }
    history = [
        SimpleNamespace(role="assistant", content="已完成", meta=json.dumps({
            "query_contract": contract,
        }, ensure_ascii=False)),
        SimpleNamespace(role="user", content="按照这个 SQL 导出", meta=""),
    ]
    assert _find_recent_assistant_query_contract(history) == contract


def test_followup_ignores_prose_without_persisted_contract():
    history = [
        SimpleNamespace(role="assistant", content="可以使用某个 SQL", meta="{}"),
    ]
    assert _find_recent_assistant_query_contract(history) == {}


def test_followup_skips_failed_placeholder_and_uses_earlier_valid_contract():
    valid = {
        "sql": 'SELECT a.id AS "标识" FROM ads.live_alpha AS a',
        "output_columns": ["标识"],
    }
    history = [
        SimpleNamespace(role="assistant", content="ok", meta=json.dumps({
            "query_contract": valid,
        }, ensure_ascii=False)),
        SimpleNamespace(role="assistant", content="failed", meta=json.dumps({
            "query_contract": {
                "sql": "WITH cohort AS (...) SELECT ...",
                "output_columns": ["标识"],
                "status": "failed",
            },
        }, ensure_ascii=False)),
    ]
    assert _find_recent_assistant_query_contract(history) == valid


def test_authoritative_contract_prefers_latest_run_sql_artifact(tmp_path, monkeypatch):
    run_id = "run-42"
    task_dir = tmp_path / "task" / run_id
    task_dir.mkdir(parents=True)
    (task_dir / "final_sql.sql").write_text(
        "```sql\nSELECT fresh_id FROM ads.live_alpha\n```\n",
        encoding="utf-8",
    )
    state = {
        "analyze_columns": ["fresh_id"],
        "export_contract": {
            "query_contract": {
                "sql": "SELECT stale_id FROM ads.live_alpha",
                "output_columns": ["fresh_id"],
                "resources": ["live_alpha"],
            },
        },
    }
    monkeypatch.setattr(
        "app.services.react_engine.ensure_workplace",
        lambda _sandbox_id: tmp_path,
    )

    contract, path = _resolve_authoritative_query_contract(
        SimpleNamespace(id="sandbox-1"),
        run_id,
        state,
    )

    assert contract["sql"] == "SELECT fresh_id FROM ads.live_alpha"
    assert contract["output_columns"] == ["fresh_id"]
    assert path == f"task/{run_id}/final_sql.sql"
