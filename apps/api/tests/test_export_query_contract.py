import asyncio
import json
import re

from app.services.export_cte_orchestrator import (
    _COUNT_ALIAS,
    _parse_rows,
    _select_sql_query_tool,
    run_cte_export,
)
from app.services.export_query_contract import (
    QueryContract,
    QueryContractParseError,
    extract_sql_resources,
    format_query_contract_methods,
    format_query_contract_sql,
    parse_column_evidence_response,
    parse_query_contract_response,
    query_contract_column_plan,
    validate_column_evidence,
    validate_query_contract,
)


def _sql_argument(args):
    return str(args.get("sql") or args.get("query") or args.get("statement") or "")


def _count_or_rows(args, rows):
    if _COUNT_ALIAS in _sql_argument(args):
        return json.dumps([{_COUNT_ALIAS: len(rows)}])
    return json.dumps(rows)


def test_query_contract_parser_skips_brace_noise_before_json():
    contract = parse_query_contract_response(
        'reasoning {draft}\n'
        '{"sql":"SELECT id AS \\"标识\\" FROM ads.live_alpha",'
        '"output_columns":["标识"],"resources":["live_alpha"]}'
    )
    assert contract.sql == 'SELECT id AS "标识" FROM ads.live_alpha'
    assert contract.output_columns == ["标识"]


def test_query_contract_parser_recovers_explicit_sql_fence():
    contract = parse_query_contract_response(
        "Here is the query:\n```sql\nSELECT id FROM ads.live_alpha\n```"
    )
    assert contract.sql == "SELECT id FROM ads.live_alpha"


def test_query_contract_parser_prefers_complete_sql_over_placeholder_fence():
    contract = parse_query_contract_response(
        "draft:\n```sql\nWITH sample AS (...) SELECT ...\n```\n"
        "final query:\n```sql\nWITH sample AS (SELECT id FROM ads.live_alpha)\n"
        'SELECT id AS "标识" FROM sample\n```'
    )
    assert "..." not in contract.sql
    assert "ads.live_alpha" in contract.sql


def test_query_contract_parser_recovers_unclosed_final_sql_fence():
    contract = parse_query_contract_response(
        "draft:\n```sql\nWITH sample AS (...) SELECT ...\n```\n"
        "final query:\n```sql\nWITH sample AS (SELECT id FROM ads.live_alpha)\n"
        'SELECT id AS "标识" FROM sample'
    )
    assert "..." not in contract.sql
    assert contract.sql.endswith('SELECT id AS "标识" FROM sample')


def test_query_contract_parser_prefers_complete_json_inside_think():
    contract = parse_query_contract_response(
        '<think>{"resources":["draft"]}</think>\n'
        '<think>{"sql":"SELECT id FROM ads.live_alpha",'
        '"output_columns":["标识"],"resources":["live_alpha"]}</think>'
    )
    assert contract.sql == "SELECT id FROM ads.live_alpha"
    assert contract.resources == ["live_alpha"]


def test_query_contract_parser_recovers_plain_sql_after_think():
    contract = parse_query_contract_response(
        "<think>compose the final query</think>\nSELECT id FROM ads.live_alpha"
    )
    assert contract.sql == "SELECT id FROM ads.live_alpha"


def test_query_contract_parser_recovers_sql_after_provider_preamble():
    contract = parse_query_contract_response(
        "I will now provide the final query.\nWITH base AS (SELECT 1 AS id)\nSELECT id FROM base"
    )
    assert contract.sql.startswith("WITH base AS")


def test_query_contract_validates_live_resources_fields_and_column_order():
    contract = QueryContract(
        sql=(
            'WITH base AS (SELECT a.id, a.value FROM ads.live_alpha AS a) '
            'SELECT id AS "标识", sum(value) AS "总值" FROM base GROUP BY id'
        ),
        output_columns=["标识", "总值"],
        resources=["live_alpha"],
    )
    result = validate_query_contract(
        contract,
        requested_columns=["标识", "总值"],
        catalog_resources=["live_alpha", "live_beta"],
        schema_fields={"live_alpha": ["id", "value"]},
    )
    assert result.ok, result.errors
    assert result.resources == ["live_alpha"]


def test_query_contract_rejects_catalog_field_and_mutation_violations():
    contract = QueryContract(
        sql="SELECT x.missing FROM ads.unknown_table AS x; DROP TABLE x",
        output_columns=["值"],
        resources=["unknown_table"],
    )
    result = validate_query_contract(
        contract,
        requested_columns=["值"],
        catalog_resources=["live_alpha"],
        schema_fields={"unknown_table": ["known"]},
    )
    assert not result.ok
    blob = "；".join(result.errors)
    assert "非只读" in blob
    assert "目录外" in blob
    assert "不存在的字段" in blob


def test_query_contract_rejects_only_top_level_pagination():
    limited = QueryContract(
        sql='SELECT a.id AS "标识" FROM ads.live_alpha AS a LIMIT 1000',
        output_columns=["标识"],
        resources=["live_alpha"],
    )
    result = validate_query_contract(
        limited,
        requested_columns=["标识"],
        catalog_resources=["live_alpha"],
        schema_fields={"live_alpha": ["id"]},
    )
    assert not result.ok
    assert "全量分页由引擎执行" in "；".join(result.errors)

    nested = QueryContract(
        sql=(
            'WITH sample AS (SELECT a.id FROM ads.live_alpha AS a LIMIT 10) '
            'SELECT id AS "标识" FROM sample'
        ),
        output_columns=["标识"],
        resources=["live_alpha"],
    )
    nested_result = validate_query_contract(
        nested,
        requested_columns=["标识"],
        catalog_resources=["live_alpha"],
        schema_fields={"live_alpha": ["id"]},
    )
    assert nested_result.ok, nested_result.errors


def test_query_contract_rejects_placeholder_sql():
    draft = QueryContract(
        sql="WITH cohort AS (...) SELECT ...",
        output_columns=["标识"],
    )
    result = validate_query_contract(
        draft,
        requested_columns=["标识"],
        catalog_resources=[],
        schema_fields={},
    )
    assert not result.ok
    assert "占位符" in "；".join(result.errors)


def test_query_contract_requires_describe_evidence_for_each_resource():
    contract = QueryContract(
        sql='SELECT b.code AS "编码" FROM ads.live_beta AS b',
        output_columns=["编码"],
        resources=["live_beta"],
    )
    result = validate_query_contract(
        contract,
        requested_columns=["编码"],
        catalog_resources=["live_beta"],
        schema_fields={},
    )
    assert not result.ok
    assert "describe 证据" in "；".join(result.errors)


def test_query_contract_sql_and_column_docs_are_first_class_outputs():
    contract = QueryContract(
        sql='SELECT id AS "标识" FROM ads.live_alpha',
        output_columns=["标识"],
        resources=["live_alpha"],
        column_evidence=[{
            "column": "标识",
            "resource": "live_alpha",
            "expression": "id",
            "reason": "字段备注说明为主键",
        }],
    )
    assert "```sql" in format_query_contract_sql(contract)
    plan = query_contract_column_plan(contract)
    assert plan[0]["数据来源"] == "`live_alpha`"
    assert "字段备注" in plan[0]["统计方法"]
    assert "### 指标统计方法" in format_query_contract_methods(contract)


def test_column_evidence_contract_requires_exact_order_methods_and_live_resources():
    evidence = parse_column_evidence_response(json.dumps({
        "column_evidence": [
            {
                "column": "标识",
                "resources": ["live_alpha"],
                "expression": "a.id",
                "reason": "直接读取主键字段",
            },
            {
                "column": "总额",
                "resources": ["live_beta"],
                "expression": "sum(b.amount)",
                "reason": "按用户聚合金额",
            },
        ],
    }, ensure_ascii=False))
    assert validate_column_evidence(
        evidence,
        requested_columns=["标识", "总额"],
        catalog_resources=["live_alpha", "live_beta"],
    ) == []
    assert validate_column_evidence(
        list(reversed(evidence)),
        requested_columns=["标识", "总额"],
        catalog_resources=["live_alpha", "live_beta"],
    )
    broken = [dict(evidence[0], resources=["missing"], reason="")]
    errors = validate_column_evidence(
        broken,
        requested_columns=["标识"],
        catalog_resources=["live_alpha"],
    )
    assert any("可复核统计方法" in error for error in errors)
    assert any("目录外资源" in error for error in errors)


def test_extract_sql_resources_ignores_cte_names():
    sql = (
        "WITH a AS (SELECT id FROM ads.live_alpha), "
        "b AS (SELECT id FROM ads.live_beta) "
        "SELECT a.id FROM a JOIN b ON a.id = b.id"
    )
    assert extract_sql_resources(sql) == ["live_alpha", "live_beta"]


def test_cte_orchestrator_uses_list_describe_count_then_page(monkeypatch):
    async def fake_select(*args, **kwargs):
        return ["live_alpha"]

    async def fake_compose(*args, **kwargs):
        return QueryContract(
            sql='SELECT a.id AS "标识" FROM ads.live_alpha AS a',
            output_columns=["标识"],
            resources=["live_alpha"],
            column_evidence=[{
                "column": "标识",
                "resource": "live_alpha",
                "expression": "a.id",
                "reason": "schema evidence",
            }],
        )

    monkeypatch.setattr(
        "app.services.export_cte_orchestrator.select_query_resources", fake_select,
    )
    monkeypatch.setattr(
        "app.services.export_cte_orchestrator.compose_query_contract", fake_compose,
    )
    calls = []

    async def call_mcp(tool, args):
        calls.append((tool, args))
        if tool == "catalog_list":
            return json.dumps([{"name": "live_alpha", "description": "test"}])
        if tool == "schema_describe":
            return json.dumps({
                "view": "live_alpha",
                "view_comment": "test rows",
                "fields": [{"name": "id", "type": "UInt64", "comment": "key"}],
            })
        if tool == "sql_execute":
            return _count_or_rows(args, [{"标识": 1}, {"标识": 2}])
        raise AssertionError(tool)

    tools = [
        {"name": "catalog_list", "description": "list table resources", "inputSchema": {"type": "object", "properties": {}}},
        {"name": "schema_describe", "description": "describe resource schema", "inputSchema": {"type": "object", "properties": {"resource_name": {"type": "string"}}}},
        {"name": "sql_execute", "description": "execute sql query", "inputSchema": {"type": "object", "properties": {"statement": {"type": "string"}}}},
    ]
    result = asyncio.run(run_cte_export(
        llm=object(),
        task_spec={"requested_columns": ["标识"]},
        tools=tools,
        call_mcp=call_mcp,
    ))
    assert result.executed
    assert result.rows == [{"标识": 1}, {"标识": 2}]
    assert [name for name, _ in calls] == [
        "catalog_list", "schema_describe", "sql_execute", "sql_execute",
    ]
    assert calls[1][1] == {"resource_name": "live_alpha"}
    assert "statement" in calls[2][1]
    assert result.mcp_call_count == 4
    assert result.expected_row_count == 2
    assert result.pages_fetched == 1


def test_cte_orchestrator_composes_complete_column_methods_before_success(monkeypatch):
    async def fake_select(*args, **kwargs):
        return ["live_alpha"]

    async def fake_compose(*args, **kwargs):
        return QueryContract(
            sql='SELECT a.id AS "标识" FROM ads.live_alpha AS a',
            output_columns=["标识"],
            resources=["live_alpha"],
        )

    async def fake_methods(*args, **kwargs):
        return [{
            "column": "标识",
            "resources": ["live_alpha"],
            "expression": "a.id",
            "reason": "直接读取 schema 中的主键字段",
        }]

    monkeypatch.setattr(
        "app.services.export_cte_orchestrator.select_query_resources", fake_select,
    )
    monkeypatch.setattr(
        "app.services.export_cte_orchestrator.compose_query_contract", fake_compose,
    )
    monkeypatch.setattr(
        "app.services.export_cte_orchestrator.compose_column_evidence", fake_methods,
    )

    async def call_mcp(tool, args):
        if tool == "catalog_list":
            return json.dumps([{"name": "live_alpha", "description": "test"}])
        if tool == "schema_describe":
            return json.dumps({
                "view": "live_alpha",
                "fields": [{"name": "id", "type": "UInt64", "comment": "key"}],
            })
        if tool == "sql_execute":
            return _count_or_rows(args, [{"标识": 1}])
        raise AssertionError(tool)

    tools = [
        {"name": "catalog_list", "description": "list table resources", "inputSchema": {"type": "object", "properties": {}}},
        {"name": "schema_describe", "description": "describe resource schema", "inputSchema": {"type": "object", "properties": {"resource_name": {"type": "string"}}}},
        {"name": "sql_execute", "description": "execute sql query", "inputSchema": {"type": "object", "properties": {"statement": {"type": "string"}}}},
    ]
    result = asyncio.run(run_cte_export(
        llm=object(),
        task_spec={"requested_columns": ["标识"]},
        tools=tools,
        call_mcp=call_mcp,
        require_column_evidence=True,
    ))
    assert result.executed
    assert result.contract.column_evidence[0]["expression"] == "a.id"


def test_cte_orchestrator_fetches_all_rows_when_mcp_caps_each_page(monkeypatch):
    async def fake_select(*args, **kwargs):
        return ["live_alpha"]

    async def fake_compose(*args, **kwargs):
        return QueryContract(
            sql='SELECT a.id AS "标识" FROM ads.live_alpha AS a',
            output_columns=["标识"],
            resources=["live_alpha"],
        )

    monkeypatch.setattr(
        "app.services.export_cte_orchestrator.select_query_resources", fake_select,
    )
    monkeypatch.setattr(
        "app.services.export_cte_orchestrator.compose_query_contract", fake_compose,
    )
    offsets = []
    outer_limits = []
    source_rows = [{"标识": value} for value in range(5)]

    async def call_mcp(tool, args):
        if tool == "catalog_list":
            return json.dumps([{"name": "live_alpha", "description": "alpha"}])
        if tool == "schema_describe":
            return json.dumps({
                "view": "live_alpha",
                "fields": [{"name": "id", "type": "UInt64"}],
            })
        sql = _sql_argument(args)
        outer_limits.append(args.get("limit"))
        if _COUNT_ALIAS in sql:
            return json.dumps([{_COUNT_ALIAS: len(source_rows)}])
        offset = int(re.search(r"\bOFFSET\s+(\d+)", sql, re.I).group(1))
        offsets.append(offset)
        return json.dumps(source_rows[offset:offset + 2])

    tools = [
        {"name": "catalog_list", "description": "list resources", "inputSchema": {"type": "object", "properties": {}}},
        {"name": "schema_describe", "description": "describe resource", "inputSchema": {"type": "object", "properties": {"resource": {"type": "string"}}}},
        {"name": "sql_execute", "description": "execute sql", "inputSchema": {"type": "object", "properties": {"sql": {"type": "string"}, "limit": {"type": "integer", "maximum": 10000}}}},
    ]
    result = asyncio.run(run_cte_export(
        llm=object(),
        task_spec={"requested_columns": ["标识"]},
        tools=tools,
        call_mcp=call_mcp,
        page_size=1000,
    ))

    assert result.executed
    assert result.rows == source_rows
    assert result.expected_row_count == 5
    assert result.pages_fetched == 3
    assert offsets == [0, 2, 4]
    assert outer_limits == [1, 1000, 1000, 1000]
    assert "LIMIT" not in result.contract.sql.upper()


def test_cte_orchestrator_accepts_verified_empty_result_without_page_query():
    sql_calls = []

    async def call_mcp(tool, args):
        if tool == "catalog_list":
            return json.dumps([{"name": "live_alpha", "description": "alpha"}])
        if tool == "schema_describe":
            return json.dumps({
                "view": "live_alpha",
                "fields": [{"name": "id", "type": "UInt64"}],
            })
        sql_calls.append(_sql_argument(args))
        return json.dumps([{_COUNT_ALIAS: 0}])

    tools = [
        {"name": "catalog_list", "description": "list resources", "inputSchema": {"type": "object", "properties": {}}},
        {"name": "schema_describe", "description": "describe resource", "inputSchema": {"type": "object", "properties": {"resource": {"type": "string"}}}},
        {"name": "sql_execute", "description": "execute sql", "inputSchema": {"type": "object", "properties": {"sql": {"type": "string"}}}},
    ]
    result = asyncio.run(run_cte_export(
        llm=None,
        task_spec={"requested_columns": ["标识"]},
        tools=tools,
        call_mcp=call_mcp,
        prior_sql='SELECT a.id AS "标识" FROM ads.live_alpha AS a',
    ))

    assert result.executed
    assert result.rows == []
    assert result.expected_row_count == 0
    assert result.pages_fetched == 0
    assert len(sql_calls) == 1
    assert _COUNT_ALIAS in sql_calls[0]


def test_cte_orchestrator_rejects_partial_rows_when_page_ends_early(monkeypatch):
    async def call_mcp(tool, args):
        if tool == "catalog_list":
            return json.dumps([{"name": "live_alpha", "description": "alpha"}])
        if tool == "schema_describe":
            return json.dumps({
                "view": "live_alpha",
                "fields": [{"name": "id", "type": "UInt64"}],
            })
        sql = _sql_argument(args)
        if _COUNT_ALIAS in sql:
            return json.dumps([{_COUNT_ALIAS: 3}])
        if "OFFSET 0" in sql:
            return json.dumps([{"标识": 1}, {"标识": 2}])
        return json.dumps([])

    tools = [
        {"name": "catalog_list", "description": "list resources", "inputSchema": {"type": "object", "properties": {}}},
        {"name": "schema_describe", "description": "describe resource", "inputSchema": {"type": "object", "properties": {"resource": {"type": "string"}}}},
        {"name": "sql_execute", "description": "execute sql", "inputSchema": {"type": "object", "properties": {"sql": {"type": "string"}}}},
    ]
    result = asyncio.run(run_cte_export(
        llm=None,
        task_spec={"requested_columns": ["标识"]},
        tools=tools,
        call_mcp=call_mcp,
        prior_sql='SELECT a.id AS "标识" FROM ads.live_alpha AS a',
    ))

    assert not result.executed
    assert result.rows == []
    assert "分页提前结束" in result.error


def test_cte_orchestrator_repairs_unparseable_model_response(monkeypatch):
    compose_calls = 0

    async def fake_select(*args, **kwargs):
        return ["live_alpha"]

    async def fake_compose(*args, **kwargs):
        nonlocal compose_calls
        compose_calls += 1
        if compose_calls == 1:
            raise QueryContractParseError("no structured contract")
        assert "QueryContractParseError" in kwargs["execution_error"]
        return QueryContract(
            sql='SELECT a.id AS "标识" FROM ads.live_alpha AS a',
            output_columns=["标识"],
            resources=["live_alpha"],
        )

    monkeypatch.setattr(
        "app.services.export_cte_orchestrator.select_query_resources", fake_select,
    )
    monkeypatch.setattr(
        "app.services.export_cte_orchestrator.compose_query_contract", fake_compose,
    )

    async def call_mcp(tool, args):
        if tool == "catalog_list":
            return json.dumps([{"name": "live_alpha", "description": "alpha"}])
        if tool == "schema_describe":
            return json.dumps({
                "view": "live_alpha",
                "fields": [{"name": "id", "type": "UInt64"}],
            })
        if tool == "sql_execute":
            return _count_or_rows(args, [{"标识": 1}])
        raise AssertionError(tool)

    tools = [
        {"name": "catalog_list", "description": "list resources", "inputSchema": {"type": "object", "properties": {}}},
        {"name": "schema_describe", "description": "describe resource", "inputSchema": {"type": "object", "properties": {"resource": {"type": "string"}}}},
        {"name": "sql_execute", "description": "execute sql", "inputSchema": {"type": "object", "properties": {"sql": {"type": "string"}}}},
    ]
    result = asyncio.run(run_cte_export(
        llm=object(),
        task_spec={"requested_columns": ["标识"]},
        tools=tools,
        call_mcp=call_mcp,
    ))

    assert result.executed
    assert result.rows == [{"标识": 1}]
    assert compose_calls == 2


def test_cte_orchestrator_normalizes_missing_output_metadata(monkeypatch):
    async def fake_select(*args, **kwargs):
        return ["live_alpha"]

    async def fake_compose(*args, **kwargs):
        return QueryContract(
            sql='SELECT a.id AS "标识" FROM ads.live_alpha AS a',
        )

    monkeypatch.setattr(
        "app.services.export_cte_orchestrator.select_query_resources", fake_select,
    )
    monkeypatch.setattr(
        "app.services.export_cte_orchestrator.compose_query_contract", fake_compose,
    )

    calls = []

    async def call_mcp(tool, args):
        calls.append(tool)
        if tool == "catalog_list":
            return json.dumps([{"name": "live_alpha", "description": "alpha"}])
        if tool == "schema_describe":
            return json.dumps({
                "view": "live_alpha",
                "fields": [{"name": "id", "type": "UInt64"}],
            })
        return _count_or_rows(args, [{"标识": 1}])

    tools = [
        {"name": "catalog_list", "description": "list resources", "inputSchema": {"type": "object", "properties": {}}},
        {"name": "schema_describe", "description": "describe resource", "inputSchema": {"type": "object", "properties": {"resource": {"type": "string"}}}},
        {"name": "sql_execute", "description": "execute sql", "inputSchema": {"type": "object", "properties": {"sql": {"type": "string"}}}},
    ]
    result = asyncio.run(run_cte_export(
        llm=object(),
        task_spec={"requested_columns": ["标识"]},
        tools=tools,
        call_mcp=call_mcp,
    ))

    assert result.executed
    assert result.contract.output_columns == ["标识"]
    assert calls == ["catalog_list", "schema_describe", "sql_execute", "sql_execute"]


def test_cte_orchestrator_reuses_prior_sql_resource_without_static_defaults(monkeypatch):
    async def fake_select(*args, **kwargs):
        return []

    monkeypatch.setattr(
        "app.services.export_cte_orchestrator.select_query_resources", fake_select,
    )

    async def call_mcp(tool, args):
        if tool == "catalog_list":
            return json.dumps([
                {"name": "live_alpha", "description": "alpha"},
                {"name": "live_beta", "description": "beta"},
            ])
        if tool == "schema_describe":
            assert args == {"resource_name": "live_beta"}
            return json.dumps({
                "view": "live_beta",
                "fields": [{"name": "code", "type": "String", "comment": "code"}],
            })
        if tool == "sql_execute":
            return _count_or_rows(args, [{"编码": "B-1"}])
        raise AssertionError(tool)

    tools = [
        {"name": "catalog_list", "description": "list table resources", "inputSchema": {"type": "object", "properties": {}}},
        {"name": "schema_describe", "description": "describe resource schema", "inputSchema": {"type": "object", "properties": {"resource_name": {"type": "string"}}}},
        {"name": "sql_execute", "description": "execute sql query", "inputSchema": {"type": "object", "properties": {"sql": {"type": "string"}}}},
    ]
    result = asyncio.run(run_cte_export(
        llm=None,
        task_spec={"requested_columns": ["编码"]},
        tools=tools,
        call_mcp=call_mcp,
        prior_sql='SELECT b.code AS "编码" FROM ads.live_beta AS b',
    ))
    assert result.executed
    assert result.contract.resources == ["live_beta"]
    assert result.contract.sql == 'SELECT b.code AS "编码" FROM ads.live_beta AS b'


def test_prior_sql_adopts_actual_result_columns_without_legacy_column_plan(monkeypatch):
    async def call_mcp(tool, args):
        if tool == "catalog_list":
            return json.dumps([{"name": "live_alpha", "description": "alpha"}])
        if tool == "schema_describe":
            return json.dumps({
                "view": "live_alpha",
                "fields": [{"name": "id", "type": "UInt64"}],
            })
        return _count_or_rows(args, [{"标识": 9}])

    tools = [
        {"name": "catalog_list", "description": "list resources", "inputSchema": {"type": "object", "properties": {}}},
        {"name": "schema_describe", "description": "describe resource", "inputSchema": {"type": "object", "properties": {"resource": {"type": "string"}}}},
        {"name": "sql_execute", "description": "execute sql", "inputSchema": {"type": "object", "properties": {"sql": {"type": "string"}}}},
    ]
    result = asyncio.run(run_cte_export(
        llm=None,
        task_spec={"requested_columns": []},
        tools=tools,
        call_mcp=call_mcp,
        prior_sql='SELECT a.id AS "标识" FROM ads.live_alpha AS a',
    ))
    assert result.executed
    assert result.contract.output_columns == ["标识"]


def test_cte_orchestrator_rejects_result_column_reordering(monkeypatch):
    async def fake_select(*args, **kwargs):
        return ["live_alpha"]

    async def fake_compose(*args, **kwargs):
        return QueryContract(
            sql='SELECT a.id AS "标识", a.value AS "值" FROM ads.live_alpha AS a',
            output_columns=["标识", "值"],
            resources=["live_alpha"],
        )

    monkeypatch.setattr(
        "app.services.export_cte_orchestrator.select_query_resources", fake_select,
    )
    monkeypatch.setattr(
        "app.services.export_cte_orchestrator.compose_query_contract", fake_compose,
    )

    async def call_mcp(tool, args):
        if tool == "catalog_list":
            return json.dumps([{"name": "live_alpha", "description": "alpha"}])
        if tool == "schema_describe":
            return json.dumps({
                "view": "live_alpha",
                "fields": [
                    {"name": "id", "type": "UInt64"},
                    {"name": "value", "type": "UInt64"},
                ],
            })
        return _count_or_rows(args, [{"值": 2, "标识": 1}])

    tools = [
        {"name": "catalog_list", "description": "list resources", "inputSchema": {"type": "object", "properties": {}}},
        {"name": "schema_describe", "description": "describe resource", "inputSchema": {"type": "object", "properties": {"resource": {"type": "string"}}}},
        {"name": "sql_execute", "description": "execute sql", "inputSchema": {"type": "object", "properties": {"sql": {"type": "string"}}}},
    ]
    result = asyncio.run(run_cte_export(
        llm=object(),
        task_spec={"requested_columns": ["标识", "值"]},
        tools=tools,
        call_mcp=call_mcp,
        max_attempts=1,
    ))
    assert not result.executed
    assert "完全同序" in result.error


def test_sql_tool_selection_prefers_full_statement_contract():
    tools = [
        {
            "name": "resource_query",
            "description": "query one resource with sql",
            "inputSchema": {
                "type": "object",
                "properties": {"view_name": {"type": "string"}, "sql": {"type": "string"}},
                "required": ["view_name", "sql"],
            },
        },
        {
            "name": "statement_execute",
            "description": "execute sql",
            "inputSchema": {
                "type": "object",
                "properties": {"statement": {"type": "string"}},
                "required": ["statement"],
            },
        },
    ]
    assert _select_sql_query_tool(tools, "resource_query") == "statement_execute"


def test_query_result_parser_accepts_mcp_wrappers_and_json_each_row():
    wrapped = json.dumps({
        "content": [{"type": "text", "text": json.dumps({"rows": [{"列": 1}]})}],
    }, ensure_ascii=False)
    assert _parse_rows(wrapped) == [{"列": 1}]
    assert _parse_rows('{"列": 1}\n{"列": 2}') == [{"列": 1}, {"列": 2}]
    assert _parse_rows('query result:\n[{"列": 3}]\ncompleted') == [{"列": 3}]
    assert _parse_rows('{"rows": []}') == []


def test_cte_orchestrator_streams_stage_progress_and_heartbeat():
    events = []

    async def on_progress(stage, detail):
        events.append((stage, dict(detail)))

    async def call_mcp(tool, args):
        if tool == "catalog_list":
            return json.dumps([{"name": "live_alpha", "description": "alpha"}])
        if tool == "schema_describe":
            return json.dumps({
                "view": "live_alpha",
                "fields": [{"name": "id", "type": "UInt64"}],
            })
        await asyncio.sleep(0.12)
        return _count_or_rows(args, [{"标识": 1}])

    tools = [
        {"name": "catalog_list", "description": "list resources", "inputSchema": {"type": "object", "properties": {}}},
        {"name": "schema_describe", "description": "describe resource", "inputSchema": {"type": "object", "properties": {"resource": {"type": "string"}}}},
        {"name": "sql_execute", "description": "execute sql", "inputSchema": {"type": "object", "properties": {"sql": {"type": "string"}}}},
    ]
    result = asyncio.run(run_cte_export(
        llm=None,
        task_spec={"requested_columns": ["标识"]},
        tools=tools,
        call_mcp=call_mcp,
        prior_sql='SELECT a.id AS "标识" FROM ads.live_alpha AS a',
        on_progress=on_progress,
        heartbeat_seconds=0.05,
    ))
    assert result.executed
    stages = [stage for stage, _ in events]
    assert stages[:2] == ["catalog_list", "catalog_list"]
    assert "schema_describe" in stages
    assert any(
        stage == "query_execute" and detail.get("state") == "heartbeat"
        for stage, detail in events
    )
    assert any(
        stage == "query_execute"
        and detail.get("state") == "progress"
        and detail.get("current") == 1
        and detail.get("total") == 1
        for stage, detail in events
    )
    assert events[-1][0] == "attempt_checkpoint"
    assert events[-1][1]["state"] == "succeeded"
    assert events[-1][1]["attempt"] == 1
    assert events[-1][1]["contract"]["sql"].startswith("SELECT")


def test_cte_orchestrator_uses_configured_rounds_after_transport_failures(monkeypatch):
    compose_calls = 0

    async def fake_select(*args, **kwargs):
        return ["live_alpha"]

    async def fake_compose(*args, **kwargs):
        nonlocal compose_calls
        compose_calls += 1
        raise RuntimeError('LLM 传输失败 (ReadTimeout): ReadTimeout("")')

    monkeypatch.setattr(
        "app.services.export_cte_orchestrator.select_query_resources", fake_select,
    )
    monkeypatch.setattr(
        "app.services.export_cte_orchestrator.compose_query_contract", fake_compose,
    )

    async def call_mcp(tool, args):
        if tool == "catalog_list":
            return json.dumps([{"name": "live_alpha", "description": "alpha"}])
        if tool == "schema_describe":
            return json.dumps({
                "view": "live_alpha",
                "fields": [{"name": "id", "type": "UInt64"}],
            })
        raise AssertionError(tool)

    tools = [
        {"name": "catalog_list", "description": "list resources", "inputSchema": {"type": "object", "properties": {}}},
        {"name": "schema_describe", "description": "describe resource", "inputSchema": {"type": "object", "properties": {"resource": {"type": "string"}}}},
        {"name": "sql_execute", "description": "execute sql", "inputSchema": {"type": "object", "properties": {"sql": {"type": "string"}}}},
    ]
    result = asyncio.run(run_cte_export(
        llm=object(),
        task_spec={"requested_columns": ["标识"]},
        tools=tools,
        call_mcp=call_mcp,
        max_attempts=3,
    ))

    assert not result.executed
    assert not result.contract.sql
    assert "ReadTimeout" in result.error
    assert compose_calls == 3
    assert result.attempts_used == 3
    assert result.attempt_limit == 3
    assert [row["stage"] for row in result.attempt_errors] == [
        "sql_compose", "sql_compose", "sql_compose",
    ]


def test_cte_orchestrator_retries_mcp_error_and_reuses_valid_contract(monkeypatch):
    compose_calls = 0
    query_calls = 0

    async def fake_select(*args, **kwargs):
        return ["live_alpha"]

    async def fake_compose(*args, **kwargs):
        nonlocal compose_calls
        compose_calls += 1
        return QueryContract(
            sql='SELECT a.id AS "标识" FROM ads.live_alpha AS a',
            output_columns=["标识"],
            resources=["live_alpha"],
        )

    monkeypatch.setattr(
        "app.services.export_cte_orchestrator.select_query_resources", fake_select,
    )
    monkeypatch.setattr(
        "app.services.export_cte_orchestrator.compose_query_contract", fake_compose,
    )

    async def call_mcp(tool, args):
        nonlocal query_calls
        if tool == "catalog_list":
            return json.dumps([{"name": "live_alpha", "description": "alpha"}])
        if tool == "schema_describe":
            return json.dumps({
                "view": "live_alpha",
                "fields": [{"name": "id", "type": "UInt64"}],
            })
        query_calls += 1
        if query_calls == 1:
            raise TimeoutError("temporary transport error")
        return _count_or_rows(args, [{"标识": 1}])

    tools = [
        {"name": "catalog_list", "description": "list resources", "inputSchema": {"type": "object", "properties": {}}},
        {"name": "schema_describe", "description": "describe resource", "inputSchema": {"type": "object", "properties": {"resource": {"type": "string"}}}},
        {"name": "sql_execute", "description": "execute sql", "inputSchema": {"type": "object", "properties": {"sql": {"type": "string"}}}},
    ]
    result = asyncio.run(run_cte_export(
        llm=object(),
        task_spec={"requested_columns": ["标识"]},
        tools=tools,
        call_mcp=call_mcp,
        max_attempts=4,
    ))

    assert result.executed
    assert result.attempts_used == 2
    assert compose_calls == 1
    assert result.attempt_errors[0]["stage"] == "query_count"


def test_cte_orchestrator_retries_catalog_mcp_error_with_same_round_budget(monkeypatch):
    list_calls = 0

    async def fake_select(*args, **kwargs):
        return ["live_alpha"]

    async def fake_compose(*args, **kwargs):
        return QueryContract(
            sql='SELECT a.id AS "标识" FROM ads.live_alpha AS a',
            output_columns=["标识"],
            resources=["live_alpha"],
        )

    monkeypatch.setattr(
        "app.services.export_cte_orchestrator.select_query_resources", fake_select,
    )
    monkeypatch.setattr(
        "app.services.export_cte_orchestrator.compose_query_contract", fake_compose,
    )

    async def call_mcp(tool, args):
        nonlocal list_calls
        if tool == "catalog_list":
            list_calls += 1
            if list_calls == 1:
                raise TimeoutError("catalog timeout")
            return json.dumps([{"name": "live_alpha", "description": "alpha"}])
        if tool == "schema_describe":
            return json.dumps({
                "view": "live_alpha",
                "fields": [{"name": "id", "type": "UInt64"}],
            })
        return _count_or_rows(args, [{"标识": 1}])

    tools = [
        {"name": "catalog_list", "description": "list resources", "inputSchema": {"type": "object", "properties": {}}},
        {"name": "schema_describe", "description": "describe resource", "inputSchema": {"type": "object", "properties": {"resource": {"type": "string"}}}},
        {"name": "sql_execute", "description": "execute sql", "inputSchema": {"type": "object", "properties": {"sql": {"type": "string"}}}},
    ]
    result = asyncio.run(run_cte_export(
        llm=object(),
        task_spec={"requested_columns": ["标识"]},
        tools=tools,
        call_mcp=call_mcp,
        max_attempts=3,
    ))

    assert result.executed
    assert result.attempts_used == 2
    assert list_calls == 2
    assert result.attempt_errors[0]["stage"] == "catalog_list"
