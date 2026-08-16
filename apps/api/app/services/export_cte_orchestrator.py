"""MCP discovery -> LLM CTE contract -> execution orchestration."""

from __future__ import annotations

import json
import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.services.export_query_contract import (
    QueryContract,
    QueryContractValidation,
    compose_column_evidence,
    compose_query_contract,
    extract_sql_resources,
    select_query_resources,
    validate_column_evidence,
    validate_query_contract,
)
from app.services.export_schema_discovery import parse_describe_schema_hint
from app.services.mcp_resource_bind import classify_catalog_tools, parse_resource_catalog


McpCall = Callable[[str, dict[str, Any]], Awaitable[str]]
ProgressCall = Callable[[str, dict[str, Any]], Awaitable[None]]


@dataclass
class CteExportResult:
    contract: QueryContract = field(default_factory=QueryContract)
    validation: QueryContractValidation = field(default_factory=QueryContractValidation)
    rows: list[dict[str, Any]] = field(default_factory=list)
    catalog: list[dict[str, str]] = field(default_factory=list)
    schemas: dict[str, dict[str, Any]] = field(default_factory=dict)
    query_tool: str = ""
    mcp_call_count: int = 0
    expected_row_count: int | None = None
    pages_fetched: int = 0
    attempts_used: int = 0
    attempt_limit: int = 0
    attempt_errors: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""

    @property
    def executed(self) -> bool:
        return self.contract.status == "executed"


def _tool_schema(tool: dict[str, Any] | None) -> dict[str, Any]:
    row = tool if isinstance(tool, dict) else {}
    schema = row.get("inputSchema") or row.get("input_schema") or row.get("parameters")
    return schema if isinstance(schema, dict) else {}


def _tool_by_name(tools: list[dict] | None, name: str) -> dict[str, Any]:
    return next(
        (
            dict(row) for row in (tools or [])
            if isinstance(row, dict) and str(row.get("name") or "").strip() == name
        ),
        {},
    )


def _resource_argument(tool: dict[str, Any], resource: str) -> dict[str, Any]:
    props = _tool_schema(tool).get("properties")
    keys = list(props.keys()) if isinstance(props, dict) else []
    preferred = next(
        (key for key in keys if key.lower() in ("view_name", "resource_name", "table_name")),
        None,
    ) or next(
        (key for key in keys if any(token in key.lower() for token in ("view", "resource", "table"))),
        None,
    )
    return {preferred: resource} if preferred else {"view_name": resource}


def _query_arguments(
    tool: dict[str, Any],
    *,
    sql: str,
    resources: list[str],
    row_limit: int | None = None,
) -> dict[str, Any]:
    schema = _tool_schema(tool)
    props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    keys = list(props.keys())
    args: dict[str, Any] = {}
    sql_key = next(
        (key for key in keys if key.lower() in ("sql", "query", "statement")),
        "sql",
    )
    args[sql_key] = sql
    resource_key = next(
        (key for key in keys if key.lower() in ("view", "view_name", "resource", "table")),
        None,
    )
    if resource_key and resources:
        args[resource_key] = resources[0]
    limit_key = next(
        (key for key in keys if key.lower() in ("limit", "page_size", "pagesize")),
        None,
    )
    if limit_key and row_limit is not None:
        args[limit_key] = max(1, int(row_limit))
    return args


def _query_page_size(tool: dict[str, Any], requested: int) -> int:
    props = _tool_schema(tool).get("properties")
    properties = props if isinstance(props, dict) else {}
    limit_schema = next(
        (
            value for key, value in properties.items()
            if key.lower() in ("limit", "page_size", "pagesize") and isinstance(value, dict)
        ),
        {},
    )
    maximum = limit_schema.get("maximum")
    try:
        return min(max(1, int(requested)), max(1, int(maximum)))
    except (TypeError, ValueError):
        return max(1, int(requested))


def _select_sql_query_tool(tools: list[dict[str, Any]], fallback: str) -> str:
    scored: list[tuple[int, str]] = []
    for row in tools or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        schema = _tool_schema(row)
        props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        required = {
            str(x).strip() for x in (schema.get("required") or []) if str(x).strip()
        }
        keys = {str(key).lower() for key in props}
        sql_keys = keys & {"sql", "query", "statement"}
        if not sql_keys:
            continue
        resource_keys = keys & {"view", "view_name", "resource", "resource_name", "table", "table_name"}
        score = 10
        if not resource_keys:
            score += 5
        if not ({str(x).lower() for x in required} & resource_keys):
            score += 3
        description = f"{name} {row.get('description') or ''}".lower()
        if "sql" in description:
            score += 2
        scored.append((score, name))
    if not scored:
        return fallback
    scored.sort(key=lambda item: (-item[0], item[1]))
    return scored[0][1]


def _parse_rows(text: str) -> list[dict[str, Any]] | None:
    raw = str(text or "").strip()
    if not raw:
        return None

    def _rows_from_value(value: Any) -> list[dict[str, Any]] | None:
        if isinstance(value, list):
            if value and all(
                isinstance(item, dict)
                and item.get("type") == "text"
                and isinstance(item.get("text"), str)
                for item in value
            ):
                for item in value:
                    nested = _parse_rows(item["text"])
                    if nested is not None:
                        return nested
            if all(isinstance(row, dict) for row in value):
                return [dict(row) for row in value]
            for item in value:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    nested = _parse_rows(item["text"])
                    if nested is not None:
                        return nested
            return None
        if isinstance(value, dict):
            for key in ("data", "rows", "records", "items", "result"):
                if key in value:
                    nested = _rows_from_value(value[key])
                    if nested is not None:
                        return nested
            content = value.get("content")
            if isinstance(content, list):
                nested = _rows_from_value(content)
                if nested is not None:
                    return nested
            if isinstance(value.get("text"), str):
                return _parse_rows(value["text"])
        if isinstance(value, str) and value.strip() != raw:
            return _parse_rows(value)
        return None

    try:
        data = json.loads(raw)
    except Exception:
        data = None
    parsed = _rows_from_value(data)
    if parsed is not None:
        return parsed

    json_each_row: list[dict[str, Any]] = []
    for line in raw.removeprefix("```json").removeprefix("```").removesuffix("```").splitlines():
        try:
            row = json.loads(line.strip())
        except Exception:
            continue
        if isinstance(row, dict):
            json_each_row.append(row)
    if json_each_row:
        return json_each_row

    decoder = json.JSONDecoder()
    for index, char in enumerate(raw):
        if char not in "[{":
            continue
        try:
            embedded, _ = decoder.raw_decode(raw[index:])
        except Exception:
            continue
        parsed = _rows_from_value(embedded)
        if parsed is not None:
            return parsed
    return None


_COUNT_ALIAS = "__export_total_rows"


def _count_query(sql: str) -> str:
    return f"SELECT count() AS {_COUNT_ALIAS} FROM ({sql}) AS __export_count"


def _page_query(sql: str, *, limit: int, offset: int) -> str:
    return (
        f"SELECT * FROM ({sql}) AS __export_page "
        f"ORDER BY ALL LIMIT {limit} OFFSET {offset}"
    )


def _parse_total_count(text: str) -> int | None:
    rows = _parse_rows(text)
    if not rows:
        return None
    row = rows[0]
    value = row.get(_COUNT_ALIAS)
    if value is None and len(row) == 1:
        value = next(iter(row.values()))
    if isinstance(value, bool):
        return None
    try:
        total = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return total if total >= 0 else None


def _is_failure(text: str) -> bool:
    raw = str(text or "").strip().lower()
    return not raw or raw.startswith("mcp 错误") or raw.startswith("mcp 调用失败") or "traceback" in raw


def _schema_payload(hint) -> dict[str, Any]:
    return {
        "view": str(getattr(hint, "view", "") or ""),
        "view_comment": str(getattr(hint, "view_comment", "") or ""),
        "fields": list(getattr(hint, "fields", None) or []),
        "comments": dict(getattr(hint, "comments", None) or {}),
        "types": dict(getattr(hint, "types", None) or {}),
    }


async def run_cte_export(
    *,
    llm,
    task_spec: dict[str, Any],
    tools: list[dict[str, Any]],
    call_mcp: McpCall,
    context_text: str = "",
    prior_sql: str = "",
    db=None,
    timeout: int = 60,
    max_attempts: int = 3,
    on_progress: ProgressCall | None = None,
    heartbeat_seconds: float = 8.0,
    page_size: int = 1000,
    require_column_evidence: bool = False,
    is_cancelled: Callable[[], bool] | None = None,
) -> CteExportResult:
    result = CteExportResult()
    page_size = max(1, int(page_size))
    attempt_limit = max(1, int(max_attempts))
    result.attempt_limit = attempt_limit

    def _cancelled() -> bool:
        return bool(is_cancelled and is_cancelled())

    async def _notify(stage: str, **detail: Any) -> None:
        if on_progress is None:
            return
        try:
            await on_progress(stage, detail)
        except Exception:
            pass

    async def _wait_stage(stage: str, awaitable, **detail: Any):
        from app.services.agent_runtime.hub import ChatStopped

        if _cancelled():
            raise ChatStopped("已停止")
        started = time.monotonic()
        await _notify(stage, state="running", elapsed_seconds=0, **detail)
        task = asyncio.ensure_future(awaitable)
        interval = max(0.05, float(heartbeat_seconds or 8.0))
        try:
            while not task.done():
                if _cancelled():
                    task.cancel()
                    try:
                        await task
                    except (asyncio.CancelledError, Exception):
                        pass
                    raise ChatStopped("已停止")
                done, _ = await asyncio.wait({task}, timeout=interval)
                if done:
                    break
                await _notify(
                    stage,
                    state="heartbeat",
                    elapsed_seconds=int(time.monotonic() - started),
                    **detail,
                )
            try:
                value = await task
            except asyncio.CancelledError as exc:
                raise ChatStopped("已停止") from exc
            except Exception:
                await _notify(
                    stage,
                    state="error",
                    elapsed_seconds=int(time.monotonic() - started),
                    **detail,
                )
                raise
            await _notify(
                stage,
                state="done",
                elapsed_seconds=int(time.monotonic() - started),
                **detail,
            )
            return value
        except ChatStopped:
            await _notify(
                stage,
                state="error",
                elapsed_seconds=int(time.monotonic() - started),
                error="已停止",
                **detail,
            )
            raise

    async def _call(
        tool_name: str,
        arguments: dict[str, Any],
        *,
        stage: str,
        **detail: Any,
    ) -> str:
        result.mcp_call_count += 1
        return await _wait_stage(
            stage,
            call_mcp(tool_name, arguments),
            **detail,
        )

    async def _record_attempt_error(stage: str, error: Any, *, attempt: int) -> bool:
        message = str(error or f"{stage} failed").strip()
        result.attempts_used = attempt
        result.error = message[:4000]
        result.attempt_errors.append({
            "attempt": attempt,
            "stage": stage,
            "error": result.error,
        })
        await _notify(
            "attempt_checkpoint",
            state="failed",
            attempt=attempt,
            total=attempt_limit,
            failed_stage=stage,
            error=result.error,
            contract=last_contract.to_dict(),
        )
        has_remaining = attempt < attempt_limit
        await _notify(
            "attempt_retry",
            state="retry" if has_remaining else "exhausted",
            attempt=attempt,
            total=attempt_limit,
            failed_stage=stage,
            error=result.error,
        )
        return has_remaining

    catalog_tools = classify_catalog_tools(tools)
    if not catalog_tools.list_tool or not catalog_tools.describe_tool or not catalog_tools.query_tool:
        result.error = "MCP 缺少 list/describe/query 类工具"
        return result

    describe_def = _tool_by_name(tools, catalog_tools.describe_tool)
    requested = [
        str(x).strip()
        for x in (task_spec.get("requested_columns") or [])
        if str(x).strip()
    ]
    query_tool = _select_sql_query_tool(tools, catalog_tools.query_tool)
    query_def = _tool_by_name(tools, query_tool)
    query_page_size = _query_page_size(query_def, page_size)
    execution_error = ""
    last_contract = QueryContract(sql=prior_sql, output_columns=requested)
    selected: list[str] = []
    reuse_contract_next = False
    for attempt in range(1, attempt_limit + 1):
        result.attempts_used = attempt
        result.expected_row_count = None
        result.pages_fetched = 0

        if not result.catalog:
            try:
                list_text = await _call(
                    catalog_tools.list_tool,
                    {},
                    stage="catalog_list",
                    attempt=attempt,
                )
            except Exception as ex:
                execution_error = f"MCP list 失败: {type(ex).__name__}: {ex}"
                await _record_attempt_error("catalog_list", execution_error, attempt=attempt)
                continue
            if _is_failure(list_text):
                execution_error = list_text or "MCP list 失败"
                await _record_attempt_error("catalog_list", execution_error, attempt=attempt)
                continue
            result.catalog = parse_resource_catalog(list_text)
            if not result.catalog:
                execution_error = "MCP list 未返回可识别资源"
                await _record_attempt_error("catalog_list", execution_error, attempt=attempt)
                continue

        if not selected:
            allowed_resources = {
                str(row.get("name") or "").strip()
                for row in result.catalog
                if isinstance(row, dict) and str(row.get("name") or "").strip()
            }
            selected = [
                name for name in extract_sql_resources(prior_sql)
                if name in allowed_resources
            ]
            if not selected and llm is not None:
                try:
                    selected = await _wait_stage(
                        "resource_select",
                        select_query_resources(
                            llm,
                            task_spec=task_spec,
                            catalog=result.catalog,
                            context_text=context_text,
                            db=db,
                            timeout=min(timeout, 40),
                        ),
                        attempt=attempt,
                    )
                except Exception as ex:
                    execution_error = f"LLM 资源选择失败: {type(ex).__name__}: {ex}"
                    await _record_attempt_error("resource_select", execution_error, attempt=attempt)
                    continue
            if not selected:
                execution_error = "LLM 未从本轮 MCP 目录选出候选资源"
                await _record_attempt_error("resource_select", execution_error, attempt=attempt)
                continue

        describe_errors: list[str] = []
        missing_schemas = [name for name in selected if name not in result.schemas]
        for resource_index, resource in enumerate(missing_schemas, 1):
            try:
                text = await _call(
                    catalog_tools.describe_tool,
                    _resource_argument(describe_def, resource),
                    stage="schema_describe",
                    attempt=attempt,
                    current=resource_index,
                    total=len(missing_schemas),
                )
            except Exception as ex:
                describe_errors.append(f"{resource}: {type(ex).__name__}: {ex}")
                continue
            if _is_failure(text):
                describe_errors.append(f"{resource}: {text or 'MCP describe 失败'}")
                continue
            hint = parse_describe_schema_hint(text, view=resource)
            if hint.fields:
                result.schemas[resource] = _schema_payload(hint)
            else:
                describe_errors.append(f"{resource}: 未返回可用字段证据")
        if not result.schemas:
            execution_error = "MCP describe 未返回可用字段证据"
            if describe_errors:
                execution_error += "；" + "；".join(describe_errors)
            await _record_attempt_error("schema_describe", execution_error, attempt=attempt)
            continue

        if last_contract.sql and (attempt == 1 or reuse_contract_next):
            contract = last_contract
            reuse_contract_next = False
        else:
            if llm is None:
                result.error = execution_error or "已有 SQL 执行失败且当前无 LLM 可修复"
                break
            try:
                contract = await _wait_stage(
                    "sql_compose",
                    compose_query_contract(
                        llm,
                        task_spec=task_spec,
                        catalog=[
                            row for row in result.catalog
                            if str(row.get("name") or "").strip() in result.schemas
                        ],
                        schemas=result.schemas,
                        context_text=context_text,
                        prior_sql=last_contract.sql,
                        execution_error=execution_error,
                        db=db,
                        timeout=timeout,
                    ),
                    attempt=attempt,
                )
            except Exception as ex:
                execution_error = f"LLM CTE 生成失败: {type(ex).__name__}: {ex}"
                await _record_attempt_error("sql_compose", execution_error, attempt=attempt)
                continue
        contract.attempts = attempt
        # Requested output names are already part of the model-authored
        # TaskSpec. If a provider returns usable SQL without repeating this
        # envelope field, normalize the protocol metadata here. The actual MCP
        # result columns are still checked exactly before the query is accepted.
        if contract.sql and not contract.output_columns:
            contract.output_columns = list(requested)
        validation = validate_query_contract(
            contract,
            requested_columns=requested,
            catalog_resources=list(result.schemas),
            schema_fields={name: list(schema.get("fields") or []) for name, schema in result.schemas.items()},
        )
        result.contract = contract
        result.validation = validation
        last_contract = contract
        if not validation.ok:
            execution_error = "；".join(validation.errors)
            contract.status = "failed"
            contract.error = execution_error
            await _record_attempt_error("sql_validate", execution_error, attempt=attempt)
            continue

        contract.resources = list(validation.resources)
        contract.status = "validated"
        if require_column_evidence:
            evidence_errors = validate_column_evidence(
                contract.column_evidence,
                requested_columns=requested,
                catalog_resources=list(result.schemas),
            )
            if evidence_errors and llm is not None:
                try:
                    contract.column_evidence = await _wait_stage(
                        "method_compose",
                        compose_column_evidence(
                            llm,
                            task_spec=task_spec,
                            contract=contract,
                            schemas=result.schemas,
                            context_text=context_text,
                            prior_error="；".join(evidence_errors),
                            db=db,
                            timeout=min(timeout, 45),
                        ),
                        attempt=attempt,
                    )
                except Exception as ex:
                    evidence_errors = [
                        f"指标统计方法生成失败: {type(ex).__name__}: {ex}"
                    ]
                else:
                    evidence_errors = validate_column_evidence(
                        contract.column_evidence,
                        requested_columns=requested,
                        catalog_resources=list(result.schemas),
                    )
            if evidence_errors:
                execution_error = "；".join(evidence_errors)
                contract.status = "failed"
                contract.error = execution_error[:4000]
                result.error = contract.error
                reuse_contract_next = True
                await _record_attempt_error("method_compose", execution_error, attempt=attempt)
                continue
        result.query_tool = query_tool
        try:
            count_text = await _call(
                query_tool,
                _query_arguments(
                    query_def,
                    sql=_count_query(contract.sql),
                    resources=contract.resources,
                    row_limit=1,
                ),
                stage="query_execute",
                attempt=attempt,
                phase="count",
            )
        except Exception as ex:
            execution_error = f"MCP COUNT 失败: {type(ex).__name__}: {ex}"
            contract.status = "failed"
            contract.error = execution_error[:4000]
            reuse_contract_next = True
            await _record_attempt_error("query_count", execution_error, attempt=attempt)
            continue
        expected_rows = _parse_total_count(count_text)
        if expected_rows is None or _is_failure(count_text):
            execution_error = count_text or "MCP query 未返回可识别的总行数"
            contract.status = "failed"
            contract.error = execution_error[:4000]
            result.error = contract.error
            await _record_attempt_error("query_count", execution_error, attempt=attempt)
            continue

        result.expected_row_count = expected_rows
        all_rows: list[dict[str, Any]] = []
        pages_fetched = 0
        page_error = ""
        while len(all_rows) < expected_rows:
            offset = len(all_rows)
            try:
                query_text = await _call(
                    query_tool,
                    _query_arguments(
                        query_def,
                        sql=_page_query(contract.sql, limit=query_page_size, offset=offset),
                        resources=contract.resources,
                        row_limit=query_page_size,
                    ),
                    stage="query_execute",
                    attempt=attempt,
                    phase="page",
                    current=offset,
                    total=expected_rows,
                )
            except Exception as ex:
                page_error = f"MCP 分页失败: {type(ex).__name__}: {ex}"
                break
            page_rows = _parse_rows(query_text)
            if page_rows is None or _is_failure(query_text):
                page_error = query_text or f"MCP query 第 {pages_fetched + 1} 页未返回结构化行"
                break
            if not page_rows:
                page_error = (
                    f"分页提前结束：COUNT={expected_rows}，实际仅取得 {len(all_rows)} 行"
                )
                break

            returned = list(page_rows[0].keys())
            if not requested:
                requested = list(returned)
                contract.output_columns = list(returned)
            if returned != requested:
                execution_error = (
                    "查询结果列必须与请求列完全同序；请求列: "
                    + ", ".join(requested)
                    + "；实际列: " + ", ".join(str(x) for x in returned)
                )
                page_error = execution_error
                break
            inconsistent = next(
                (
                    list(row.keys()) for row in page_rows
                    if list(row.keys()) != requested
                ),
                None,
            )
            if inconsistent is not None:
                page_error = (
                    "查询结果页内列结构不一致；请求列: "
                    + ", ".join(requested)
                    + "；异常列: " + ", ".join(str(x) for x in inconsistent)
                )
                break
            if len(all_rows) + len(page_rows) > expected_rows:
                page_error = (
                    f"分页行数超过 COUNT：COUNT={expected_rows}，"
                    f"累计将达到 {len(all_rows) + len(page_rows)} 行"
                )
                break

            all_rows.extend(page_rows)
            pages_fetched += 1
            result.pages_fetched = pages_fetched
            await _notify(
                "query_execute",
                state="progress",
                phase="page",
                current=len(all_rows),
                total=expected_rows,
            )

        if not page_error and len(all_rows) == expected_rows:
            result.rows = all_rows
            contract.status = "executed"
            contract.error = ""
            result.error = ""
            await _notify(
                "attempt_checkpoint",
                state="succeeded",
                attempt=attempt,
                total=attempt_limit,
                row_count=len(all_rows),
                contract=contract.to_dict(),
            )
            return result

        if not page_error:
            page_error = (
                f"分页完整性校验失败：COUNT={expected_rows}，实际取得 {len(all_rows)} 行"
            )
        execution_error = page_error
        contract.status = "failed"
        contract.error = execution_error[:4000]
        result.error = contract.error
        if page_error.startswith("MCP 分页失败:"):
            reuse_contract_next = True
        await _record_attempt_error("query_page", execution_error, attempt=attempt)

    if not result.error:
        result.error = result.contract.error or "CTE 查询未执行成功"
    return result
