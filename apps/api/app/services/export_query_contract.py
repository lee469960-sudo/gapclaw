"""LLM-authored SQL contract backed by live MCP catalog/schema evidence."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from app.services.intent_router import _extract_json_object


logger = logging.getLogger(__name__)


_MUTATING_SQL_RE = re.compile(
    r"\b(?:insert|update|delete|drop|alter|truncate|create|replace|attach|detach|"
    r"optimize|rename|grant|revoke)\b",
    re.I,
)
_BASE_RESOURCE_RE = re.compile(
    r"\b(?:from|join)\s+(?:ads\.)?([A-Za-z_][A-Za-z0-9_]*)"
    r"(?:\s+(?:as\s+)?([A-Za-z_][A-Za-z0-9_]*))?",
    re.I,
)
_CTE_NAME_RE = re.compile(
    r"(?:\bwith\b|,)\s*([A-Za-z_][A-Za-z0-9_]*)\s+as\s*\(",
    re.I,
)
_QUALIFIED_FIELD_RE = re.compile(
    r"\b([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\b"
)


def normalize_sql(text: str) -> str:
    sql = str(text or "").strip()
    sql = re.sub(r"^```(?:sql)?\s*", "", sql, flags=re.I)
    sql = re.sub(r"\s*```$", "", sql)
    return sql.strip().rstrip(";").strip()


def _recover_sql_candidate(text: str) -> str:
    raw = str(text or "")
    starts = list(re.finditer(r"(?im)^\s*(?:with|select)\b", raw))
    if not starts:
        return ""
    return normalize_sql(raw[starts[0].start():])


def _contains_sql_placeholder(sql: str) -> bool:
    text = normalize_sql(sql)
    quote = ""
    index = 0
    while index < len(text):
        char = text[index]
        following = text[index + 1] if index + 1 < len(text) else ""
        if quote:
            if char == quote:
                if following == quote:
                    index += 2
                    continue
                quote = ""
            elif char == "\\" and following:
                index += 2
                continue
            index += 1
            continue
        if char in ("'", '"', "`"):
            quote = char
            index += 1
            continue
        if char == "-" and following == "-":
            newline = text.find("\n", index + 2)
            index = len(text) if newline < 0 else newline + 1
            continue
        if char == "/" and following == "*":
            end = text.find("*/", index + 2)
            index = len(text) if end < 0 else end + 2
            continue
        if text.startswith("...", index) or char == "…":
            return True
        index += 1
    return False


def is_executable_query_sql(sql: str) -> bool:
    text = normalize_sql(sql)
    return bool(
        re.match(r"^(?:with|select)\b", text, re.I)
        and re.search(r"\bselect\b", text, re.I)
        and not _contains_sql_placeholder(text)
    )


@dataclass
class QueryContract:
    sql: str = ""
    output_columns: list[str] = field(default_factory=list)
    resources: list[str] = field(default_factory=list)
    column_evidence: list[dict[str, Any]] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    status: str = "draft"  # draft | validated | executed | failed
    error: str = ""
    attempts: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> "QueryContract":
        data = payload if isinstance(payload, dict) else {}
        return cls(
            sql=normalize_sql(str(data.get("sql") or "")),
            output_columns=[
                str(x).strip()
                for x in (data.get("output_columns") or [])
                if str(x).strip()
            ],
            resources=list(dict.fromkeys(
                str(x).strip()
                for x in (data.get("resources") or [])
                if str(x).strip()
            )),
            column_evidence=[
                dict(x) for x in (data.get("column_evidence") or [])
                if isinstance(x, dict)
            ],
            assumptions=[
                str(x).strip() for x in (data.get("assumptions") or [])
                if str(x).strip()
            ],
        )


class QueryContractParseError(ValueError):
    """The model responded, but no usable query contract could be decoded."""


class ColumnEvidenceParseError(ValueError):
    """The model did not return a complete per-column method contract."""


def _query_contract_payloads(text: str) -> list[dict[str, Any]]:
    """Return contract-shaped JSON objects without discarding think wrappers."""
    raw = str(text or "")
    decoder = json.JSONDecoder()
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, char in enumerate(raw):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(raw[index:])
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(value, dict):
            continue
        if not any(
            key in value
            for key in ("sql", "output_columns", "resources", "column_evidence")
        ):
            continue
        marker = json.dumps(value, ensure_ascii=False, sort_keys=True)
        if marker not in seen:
            seen.add(marker)
            candidates.append(value)
    return candidates


def _sql_fence_candidates(text: str) -> list[str]:
    raw = str(text or "")
    openings = list(re.finditer(r"```(?:sql|clickhouse)\s*", raw, re.I))
    candidates: list[str] = []
    for opening in openings:
        end = raw.find("```", opening.end())
        body = raw[opening.end():] if end < 0 else raw[opening.end():end]
        sql = _recover_sql_candidate(body)
        if sql:
            candidates.append(sql)
    return candidates


def parse_query_contract_response(text: str) -> QueryContract:
    raw = str(text or "").strip()
    payloads = _query_contract_payloads(raw)
    if not payloads:
        generic = _extract_json_object(raw)
        if isinstance(generic, dict):
            payloads.append(generic)
    if payloads:
        contracts = [QueryContract.from_payload(payload) for payload in payloads]
        # Prefer a complete later answer over an earlier draft object.
        contracts.sort(
            key=lambda item: (
                bool(item.sql),
                len(item.output_columns),
                len(item.resources),
                len(item.column_evidence),
            ),
        )
        contract = contracts[-1]
        if contract.sql or any(
            key in payloads[-1]
            for key in ("sql", "output_columns", "resources", "column_evidence")
        ):
            return contract

    # Some providers complete the SQL task but omit the JSON envelope. Recover
    # only an explicit SQL fence; the normal contract validator remains the
    # authority for read-only SQL, MCP resources, described fields and columns.
    matches = re.findall(r"```(?:sql|clickhouse)\s*([\s\S]*?)```", raw, re.I)
    fenced_candidates = _sql_fence_candidates(raw)
    for sql in reversed(fenced_candidates):
        if is_executable_query_sql(sql):
            return QueryContract(sql=sql)
    for sql in reversed(fenced_candidates):
        if re.match(r"^(?:with|select)\b", sql, re.I):
            return QueryContract(sql=sql)

    visible = re.sub(r"<think\b[^>]*>[\s\S]*?</think>", "", raw, flags=re.I)
    visible = re.sub(r"</?think\b[^>]*>", "", visible, flags=re.I).strip()
    plain_sql = normalize_sql(visible)
    if re.match(r"^(?:with|select)\b", plain_sql, re.I):
        return QueryContract(sql=plain_sql)
    sql_start = list(re.finditer(r"(?im)^\s*(?:with|select)\b", visible))
    if sql_start:
        plain_sql = normalize_sql(visible[sql_start[0].start():])
        if re.match(r"^(?:with|select)\b", plain_sql, re.I):
            return QueryContract(sql=plain_sql)

    shape = (
        f"chars={len(raw)}, braces={raw.count('{')}/{raw.count('}')}, "
        f"fenced_sql={bool(matches)}, plain_sql={bool(plain_sql)}, "
        f"json_candidates={len(payloads)}"
    )
    logger.warning("query contract response parse failed: %s", shape)
    raise QueryContractParseError(
        "模型响应中没有可解析的 QueryContract JSON 或 SQL 代码块；" + shape
    )


@dataclass
class QueryContractValidation:
    ok: bool = False
    errors: list[str] = field(default_factory=list)
    resources: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def extract_sql_resources(sql: str) -> list[str]:
    text = normalize_sql(sql)
    ctes = {m.group(1).lower() for m in _CTE_NAME_RE.finditer(text)}
    out: list[str] = []
    for match in _BASE_RESOURCE_RE.finditer(text):
        name = str(match.group(1) or "").strip()
        if not name or name.lower() in ctes or name.lower() in ("select", "values"):
            continue
        if name not in out:
            out.append(name)
    return out


def _top_level_sql_words(sql: str) -> list[str]:
    """Tokenize top-level words while ignoring literals, comments and subqueries."""
    text = normalize_sql(sql)
    words: list[str] = []
    depth = 0
    index = 0
    quote = ""
    while index < len(text):
        char = text[index]
        following = text[index + 1] if index + 1 < len(text) else ""
        if quote:
            if char == quote:
                if following == quote:
                    index += 2
                    continue
                quote = ""
            elif char == "\\" and following:
                index += 2
                continue
            index += 1
            continue
        if char in ("'", '"', "`"):
            quote = char
            index += 1
            continue
        if char == "-" and following == "-":
            newline = text.find("\n", index + 2)
            index = len(text) if newline < 0 else newline + 1
            continue
        if char == "/" and following == "*":
            end = text.find("*/", index + 2)
            index = len(text) if end < 0 else end + 2
            continue
        if char == "(":
            depth += 1
            index += 1
            continue
        if char == ")":
            depth = max(0, depth - 1)
            index += 1
            continue
        if depth == 0 and (char.isalpha() or char == "_"):
            end = index + 1
            while end < len(text) and (text[end].isalnum() or text[end] == "_"):
                end += 1
            words.append(text[index:end].lower())
            index = end
            continue
        index += 1
    return words


def validate_query_contract(
    contract: QueryContract,
    *,
    requested_columns: list[str] | None,
    catalog_resources: list[str] | set[str] | None,
    schema_fields: dict[str, list[str]] | None = None,
) -> QueryContractValidation:
    """Validate platform invariants without deciding business semantics."""
    errors: list[str] = []
    sql = normalize_sql(contract.sql)
    requested = [str(x).strip() for x in (requested_columns or []) if str(x).strip()]
    allowed = {str(x).strip() for x in (catalog_resources or []) if str(x).strip()}
    fields_by_resource = {
        str(k).strip(): {str(f).strip().lower() for f in (v or []) if str(f).strip()}
        for k, v in (schema_fields or {}).items()
        if str(k).strip()
    }

    if not sql or not re.match(r"^(?:with|select)\b", sql, re.I):
        errors.append("SQL 必须是以 WITH 或 SELECT 开始的只读查询")
    if sql and _contains_sql_placeholder(sql):
        errors.append("SQL 仍包含省略号或占位符，必须返回完整可执行查询")
    if _MUTATING_SQL_RE.search(sql):
        errors.append("SQL 包含非只读语句")
    if ";" in sql:
        errors.append("SQL 只能包含一条语句")
    pagination_words = {"limit", "offset"} & set(_top_level_sql_words(sql))
    if pagination_words:
        errors.append("最终查询不能包含顶层 LIMIT/OFFSET；全量分页由引擎执行")
    if requested and contract.output_columns != requested:
        errors.append("output_columns 必须与用户请求列完全同序")

    resources = extract_sql_resources(sql)
    unknown_resources = [
        r for r in resources
        if catalog_resources is not None and r not in allowed
    ]
    if unknown_resources:
        errors.append("SQL 引用了本轮 MCP 目录外资源: " + ", ".join(unknown_resources))
    if schema_fields is not None:
        undescribed = [resource for resource in resources if resource not in fields_by_resource]
        if undescribed:
            errors.append("SQL 引用了尚未取得 describe 证据的资源: " + ", ".join(undescribed))
    if contract.resources and set(contract.resources) != set(resources):
        errors.append("resources 必须与 SQL 实际 FROM/JOIN 资源一致")

    alias_to_resource: dict[str, str] = {}
    for match in _BASE_RESOURCE_RE.finditer(sql):
        resource = str(match.group(1) or "").strip()
        alias = str(match.group(2) or resource).strip()
        if resource in resources:
            alias_to_resource[alias.lower()] = resource
            alias_to_resource.setdefault(resource.lower(), resource)
    unknown_fields: list[str] = []
    for alias, field_name in _QUALIFIED_FIELD_RE.findall(sql):
        resource = alias_to_resource.get(alias.lower())
        available = fields_by_resource.get(resource or "")
        if resource and available and field_name.lower() not in available:
            unknown_fields.append(f"{alias}.{field_name}")
    if unknown_fields:
        errors.append("SQL 引用了 describe 中不存在的字段: " + ", ".join(dict.fromkeys(unknown_fields)))

    return QueryContractValidation(ok=not errors, errors=errors, resources=resources)


_COMPOSE_SYSTEM = """你是资深 ClickHouse DBA。根据用户 TaskSpec 和本轮 MCP list/describe 证据，生成一条完整可执行的 CTE SQL。

要求：
1. 从整体任务推理 cohort、聚合和 JOIN，一次生成完整 CTE；不要逐列生成独立分页查询。
2. 资源只能来自 catalog，字段只能来自 describe；优先依据 view_comment 和字段备注理解语义。
3. 用户明确指定资源时视为高优先级意图，但仍须与本轮 catalog/describe 一致。
4. 最终 SELECT 的中文别名必须与 requested_columns 完全一致且同序。
5. 只读、单语句，不带 FORMAT、LIMIT、OFFSET，不带分号；全量分页由引擎执行。
6. 只输出 SQL 正文，以 WITH 或 SELECT 开始；不要 JSON、Markdown、解释或思考过程。合同元数据由引擎从 TaskSpec 和 SQL 确定。
"""

_RESOURCE_SELECT_SYSTEM = """你是数据资源发现规划器。只输出 JSON 对象，不要 Markdown。

根据 TaskSpec、用户原始需求和本轮 MCP 资源目录，选出生成一条完整 CTE SQL 所需的候选资源：
{"resources":["目录中的真实资源名"],"reason":"简短说明"}

规则：
1. 只能选择 catalog 中存在的资源；不确定时可以多选语义相近候选，等待 describe 后再决策。
2. 使用目录描述理解业务语义，不依据固定角色模板或历史默认视图。
3. 用户明确指定的资源若存在于 catalog，应纳入候选。
4. 控制数量，只选完成任务可能需要的资源。
"""

_COLUMN_EVIDENCE_SYSTEM = """你是 SQL 口径审计器。根据已校验的 ClickHouse SQL、用户请求列和本轮 MCP schema，逐列说明统计方法。

只输出一个 JSON 对象，不要 Markdown、SQL 或思考过程：
{"column_evidence":[{"column":"与 requested_columns 完全一致","resources":["SQL 实际使用的资源名"],"expression":"最终 SELECT 对应表达式","reason":"说明人群、时间字段与时区、过滤条件、聚合/去重、单位换算和空值处理；不适用的项目可省略"}]}

要求：
1. column_evidence 必须覆盖 requested_columns 的每一列，数量和顺序完全一致，不得合并或补造列。
2. 只能引用 SQL 和 schemas 中真实存在的资源、字段和条件；不得套用历史模板。
3. expression 必须引用最终 SQL 的真实表达式或别名来源，禁止写“见 SQL”“按需求统计”等空泛描述。
4. reason 必须让用户能复核该列如何得到；直接字段也要说明来源和格式化方式。
5. 用户对上一轮有质疑时，必须重新审读 SQL 与 schema 后给出本轮证据，不得复述旧结论。
"""


async def select_query_resources(
    llm,
    *,
    task_spec: dict[str, Any],
    catalog: list[dict[str, Any]],
    context_text: str = "",
    db=None,
    timeout: int = 40,
) -> list[str]:
    from app.services.llm_client import chat_completion

    allowed = {
        str(row.get("name") or "").strip()
        for row in catalog
        if isinstance(row, dict) and str(row.get("name") or "").strip()
    }
    raw = await chat_completion(
        llm,
        [
            {"role": "system", "content": _RESOURCE_SELECT_SYSTEM},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "task_spec": task_spec,
                        "catalog": catalog,
                        "user_context": str(context_text or "")[:6000],
                    },
                    ensure_ascii=False,
                ),
            },
        ],
        max_tokens=1200,
        db=db,
        timeout=timeout,
    )
    data = _extract_json_object(raw) or {}
    return list(dict.fromkeys(
        str(name).strip()
        for name in (data.get("resources") or [])
        if str(name).strip() in allowed
    ))


async def compose_query_contract(
    llm,
    *,
    task_spec: dict[str, Any],
    catalog: list[dict[str, Any]],
    schemas: dict[str, Any],
    context_text: str = "",
    prior_sql: str = "",
    execution_error: str = "",
    db=None,
    timeout: int = 60,
) -> QueryContract:
    from app.services.llm_client import chat_completion

    payload = {
        "task_spec": task_spec,
        "catalog": catalog,
        "schemas": schemas,
        "user_context": str(context_text or "")[:6000],
        "prior_sql": normalize_sql(prior_sql)[:20000],
        "execution_error": str(execution_error or "")[:4000],
    }
    raw = await chat_completion(
        llm,
        [
            {"role": "system", "content": _COMPOSE_SYSTEM},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        max_tokens=6000,
        db=db,
        timeout=timeout,
    )
    return parse_query_contract_response(raw)


def parse_column_evidence_response(text: str) -> list[dict[str, Any]]:
    raw = str(text or "").strip()
    data = _extract_json_object(raw)
    rows: Any = data.get("column_evidence") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise ColumnEvidenceParseError("模型响应缺少 column_evidence 数组")
    return [dict(row) for row in rows if isinstance(row, dict)]


def validate_column_evidence(
    evidence: list[dict[str, Any]] | None,
    *,
    requested_columns: list[str] | None,
    catalog_resources: list[str] | set[str] | None,
) -> list[str]:
    """Validate evidence shape and provenance without business semantics."""
    requested = [str(x).strip() for x in (requested_columns or []) if str(x).strip()]
    rows = [row for row in (evidence or []) if isinstance(row, dict)]
    columns = [str(row.get("column") or "").strip() for row in rows]
    errors: list[str] = []
    if columns != requested:
        errors.append("column_evidence 必须与用户请求列完全同序覆盖")
    allowed = {
        str(name).strip() for name in (catalog_resources or []) if str(name).strip()
    }
    for index, row in enumerate(rows):
        column = columns[index] if index < len(columns) else ""
        expression = str(row.get("expression") or "").strip()
        reason = str(row.get("reason") or "").strip()
        if not expression:
            errors.append(f"{column or f'第 {index + 1} 列'}缺少 SQL 表达式")
        if not reason:
            errors.append(f"{column or f'第 {index + 1} 列'}缺少可复核统计方法")
        raw_resources = row.get("resources")
        resources = (
            [str(x).strip() for x in raw_resources if str(x).strip()]
            if isinstance(raw_resources, list)
            else [str(row.get("resource") or "").strip()]
        )
        resources = [name for name in resources if name]
        if allowed and not resources:
            errors.append(f"{column or f'第 {index + 1} 列'}缺少数据来源")
        unknown = [name for name in resources if name and name not in allowed]
        if unknown:
            errors.append(
                f"{column or f'第 {index + 1} 列'}引用本轮 MCP 目录外资源: "
                + ", ".join(unknown)
            )
    return errors


async def compose_column_evidence(
    llm,
    *,
    task_spec: dict[str, Any],
    contract: QueryContract,
    schemas: dict[str, Any],
    context_text: str = "",
    prior_error: str = "",
    db=None,
    timeout: int = 45,
) -> list[dict[str, Any]]:
    from app.services.llm_client import chat_completion

    payload = {
        "task_spec": task_spec,
        "requested_columns": list(contract.output_columns),
        "sql": normalize_sql(contract.sql),
        "schemas": schemas,
        "user_context": str(context_text or "")[:6000],
        "prior_error": str(prior_error or "")[:2000],
    }
    raw = await chat_completion(
        llm,
        [
            {"role": "system", "content": _COLUMN_EVIDENCE_SYSTEM},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        max_tokens=3000,
        db=db,
        timeout=timeout,
    )
    return parse_column_evidence_response(raw)


def format_query_contract_sql(contract: QueryContract | dict[str, Any] | None) -> str:
    data = contract.to_dict() if isinstance(contract, QueryContract) else dict(contract or {})
    sql = normalize_sql(str(data.get("sql") or ""))
    return f"### SQL\n\n```sql\n{sql}\n```" if sql else ""


def query_contract_column_plan(
    contract: QueryContract | dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Build presentation-only column docs from LLM evidence."""
    data = contract.to_dict() if isinstance(contract, QueryContract) else dict(contract or {})
    evidence = {
        str(row.get("column") or "").strip(): row
        for row in (data.get("column_evidence") or [])
        if isinstance(row, dict) and str(row.get("column") or "").strip()
    }
    out: list[dict[str, Any]] = []
    for header in data.get("output_columns") or []:
        name = str(header or "").strip()
        if not name:
            continue
        row = evidence.get(name) or {}
        raw_resources = row.get("resources")
        resources = (
            [str(x).strip() for x in raw_resources if str(x).strip()]
            if isinstance(raw_resources, list)
            else [str(row.get("resource") or "").strip()]
        )
        resources = list(dict.fromkeys(x for x in resources if x))
        expression = str(row.get("expression") or "").strip()
        reason = str(row.get("reason") or "").strip()
        method = expression
        if reason:
            method = f"{expression}；{reason}" if expression else reason
        out.append({
            "header": name,
            "kind": "sql_output",
            "fetch_mode": "query_contract",
            "sources": [
                {"view": resource, "role": "query_contract", "fields": []}
                for resource in resources
            ],
            "agg_spec": {},
            "数据来源": (
                "、".join(f"`{resource}`" for resource in resources)
                if resources else "由最终 CTE 推导"
            ),
            "统计方法": method or "见最终 CTE SQL",
            "binding_status": "bound" if resources else "derived",
            "rule": "query_contract",
        })
    return out


def format_query_contract_methods(
    contract: QueryContract | dict[str, Any] | None,
) -> str:
    plan = query_contract_column_plan(contract)
    if not plan:
        return ""

    def _cell(value: Any) -> str:
        return str(value or "").replace("|", "\\|").replace("\n", " ").strip()

    lines = [
        "### 指标统计方法",
        "",
        "| # | 指标列 | 数据来源 | 统计方法 |",
        "|---:|---|---|---|",
    ]
    for index, row in enumerate(plan, 1):
        lines.append(
            f"| {index} | {_cell(row.get('header'))} | "
            f"{_cell(row.get('数据来源'))} | {_cell(row.get('统计方法'))} |"
        )
    return "\n".join(lines)
