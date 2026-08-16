"""Minimal NDJSON MCP stdio server for system logs (stdlib only)."""

from __future__ import annotations

import json
import sys
from typing import Any

from mcp_servers.system_logs.tools import dispatch

PROTOCOL_VERSION = "2024-11-05"

TOOLS = [
    {
        "name": "list_log_sources",
        "description": "列出可读的 GAP 本地日志源（api/web/cloudflared/im_events）及文件是否存在。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "tail_log",
        "description": "读取指定日志源末尾若干行；可选 grep 过滤。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": "api | web | cloudflared | im_events",
                    "default": "api",
                },
                "lines": {"type": "integer", "description": "行数，默认 200，最大 2000", "default": 200},
                "grep": {"type": "string", "description": "可选过滤关键字或正则", "default": ""},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "search_log",
        "description": "按关键字/级别搜索日志（ERROR、Traceback、WARNING 等）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "default": "api"},
                "query": {"type": "string", "description": "关键字", "default": ""},
                "level": {
                    "type": "string",
                    "description": "ERROR | WARNING | TRACEBACK | INFO 等",
                    "default": "",
                },
                "minutes": {"type": "integer", "description": "仅最近 N 分钟（尽力按行内时间戳）", "default": 0},
                "limit": {"type": "integer", "default": 100},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "log_stats",
        "description": "汇总错误级别计数、Top 异常、HTTP 状态码分布（api.log）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "default": "api"},
                "minutes": {"type": "integer", "description": "统计窗口分钟数，默认 60", "default": 60},
            },
            "additionalProperties": False,
        },
    },
]


def _write(msg: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _result_text(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


def _handle(msg: dict[str, Any]) -> None:
    method = msg.get("method")
    msg_id = msg.get("id")
    params = msg.get("params") or {}

    # Notifications (no id)
    if msg_id is None:
        return

    if method == "initialize":
        _write({
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "system-logs", "version": "1.0.0"},
            },
        })
        return

    if method == "tools/list":
        _write({"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}})
        return

    if method == "tools/call":
        name = str(params.get("name") or "")
        arguments = params.get("arguments") or {}
        try:
            text = dispatch(name, arguments if isinstance(arguments, dict) else {})
        except Exception as e:
            text = f"MCP 错误: {e}"
        _write({"jsonrpc": "2.0", "id": msg_id, "result": _result_text(text)})
        return

    if method == "ping":
        _write({"jsonrpc": "2.0", "id": msg_id, "result": {}})
        return

    _write({
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    })


def main() -> None:
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(msg, dict):
            continue
        method = msg.get("method")
        if method == "notifications/initialized" or (
            isinstance(method, str) and method.startswith("notifications/")
        ):
            continue
        _handle(msg)


if __name__ == "__main__":
    main()
