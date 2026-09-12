"""MCP Streamable HTTP / stdio / legacy HTTP client."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

MCP_ACCEPT = "application/json, text/event-stream"
MCP_PROTOCOL_VERSION = "2024-11-05"
SESSION_HEADER = "mcp-session-id"
# Large query_ads_view responses routinely exceed 60s on remote ADS MCP.
_STREAMABLE_CALL_TIMEOUT = httpx.Timeout(180.0, connect=30.0)
_STREAMABLE_LIST_TIMEOUT = httpx.Timeout(60.0, connect=20.0)
_TRANSPORT_RETRY_MAX = 3  # includes first attempt
_TRANSPORT_RETRY_BACKOFF_S = (0.5, 1.5)
_QUERY_LIKE_TOOLS = frozenset({
    "query_ads_view",
    "query_ads_metric",
    "query_view",
    "query_resource",
    "run_query",
})


def _parse_headers(raw: str) -> dict:
    try:
        data = json.loads(raw or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _parse_json_obj(raw: str | None, default):
    try:
        data = json.loads(raw or "")
        return data if isinstance(data, type(default)) else default
    except Exception:
        return default


def _parse_sse_json(text: str, expect_id: int | str | None = None) -> dict | None:
    if not text:
        return None
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            msg = json.loads(stripped)
            if expect_id is not None and msg.get("id") != expect_id:
                return None
            return msg
        except Exception:
            pass
    for block in re.split(r"\n\n+", stripped):
        data_line = None
        for line in block.splitlines():
            if line.startswith("data:"):
                data_line = line[5:].strip()
                break
        if not data_line:
            continue
        try:
            msg = json.loads(data_line)
        except Exception:
            continue
        if expect_id is not None and msg.get("id") != expect_id:
            continue
        return msg
    return None


def _is_streamable(protocol: str) -> bool:
    return (protocol or "sse").lower() in ("sse", "streamable", "streamable-http", "http+sse")


def _is_stdio(protocol: str) -> bool:
    return (protocol or "").lower() in ("stdio", "command", "npx", "local")


def _normalize_tools(data) -> list[dict]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        tools = data.get("tools")
        if isinstance(tools, list):
            return tools
        result = data.get("result")
        if isinstance(result, dict) and isinstance(result.get("tools"), list):
            return result["tools"]
    return []


def _tool_result_text(result: dict | None) -> str:
    if not result:
        return "MCP 无响应"
    if "error" in result:
        err = result["error"]
        if isinstance(err, dict):
            return f"MCP 错误: {err.get('message', err)}"
        return f"MCP 错误: {err}"
    payload = result.get("result", result)
    if isinstance(payload, dict):
        # MCP isError content (e.g. getnote "Unknown tool: xxx")
        if payload.get("isError"):
            content = payload.get("content")
            if isinstance(content, list):
                parts = [
                    str(item.get("text", ""))
                    for item in content
                    if isinstance(item, dict) and item.get("type") == "text"
                ]
                if parts:
                    return f"MCP 错误: {' '.join(parts)}"
        content = payload.get("content")
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
            if parts:
                return "\n".join(parts)
        return json.dumps(payload, ensure_ascii=False)
    return json.dumps(result, ensure_ascii=False)


def _looks_like_unknown_tool(text: str) -> bool:
    t = (text or "").lower()
    return "unknown tool" in t or "tool not found" in t or "not found" in t and "tool" in t


def _format_available_tools(tools: list[dict], limit: int = 40) -> str:
    names = [str(t.get("name")) for t in tools if isinstance(t, dict) and t.get("name")]
    if not names:
        return ""
    shown = names[:limit]
    suffix = f" …共 {len(names)} 个" if len(names) > limit else ""
    return "可用工具: " + ", ".join(shown) + suffix


async def _append_tool_catalog_hint(session: _StdioSession, text: str) -> str:
    if not _looks_like_unknown_tool(text):
        return text
    try:
        listed = await session.request("tools/list", {})
        hint = _format_available_tools(_normalize_tools(listed))
        if hint:
            return f"{text}\n{hint}\n请改用上列真实工具名，例如: MCP: list_notes {{\"since_id\":0}}"
    except Exception:
        pass
    return text


def _looks_like_html_404(text: str) -> bool:
    t = (text or "").lower()
    return "路由未注册" in (text or "") or "<html" in t or "404" in t[:80]


def _short_err_body(text: str, limit: int = 200) -> str:
    if _looks_like_html_404(text):
        if "路由未注册" in (text or ""):
            return "HTTP 404 路由未注册（该 URL 不是 MCP Streamable 端点；得到大脑请用 stdio: npx @getnote/mcp）"
        return "HTTP 404 HTML 错误页"
    return (text or "").replace("\n", " ")[:limit]


# ---------- Streamable HTTP ----------


class _StreamableSession:
    def __init__(self, url: str, headers: dict):
        self.url = url
        self.base_headers = {
            "Content-Type": "application/json",
            "Accept": MCP_ACCEPT,
            **headers,
        }
        self.session_id: str | None = None
        self._next_id = 1

    def _headers(self) -> dict:
        h = dict(self.base_headers)
        if self.session_id:
            # Some servers reject duplicate cased session headers; send one canonical name.
            h[SESSION_HEADER] = self.session_id
        return h

    def _alloc_id(self) -> int:
        i = self._next_id
        self._next_id += 1
        return i

    async def _post(self, client: httpx.AsyncClient, payload: dict) -> tuple[httpx.Response, dict | None]:
        resp = await client.post(self.url, headers=self._headers(), json=payload)
        sid = (
            resp.headers.get(SESSION_HEADER)
            or resp.headers.get("Mcp-Session-Id")
            or resp.headers.get("MCP-Session-Id")
        )
        if sid:
            self.session_id = sid
        msg = None
        if resp.status_code < 400:
            msg = _parse_sse_json(resp.text, payload.get("id"))
        return resp, msg

    async def initialize(self, client: httpx.AsyncClient) -> dict | None:
        payload = {
            "jsonrpc": "2.0",
            "id": self._alloc_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "gap", "version": "1.0"},
            },
        }
        resp, msg = await self._post(client, payload)
        if resp.status_code >= 400:
            return {
                "error": {
                    "message": f"initialize HTTP {resp.status_code}: {_short_err_body(resp.text)}",
                    "code": resp.status_code,
                }
            }
        await client.post(
            self.url,
            headers=self._headers(),
            json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        )
        return msg

    async def rpc(self, client: httpx.AsyncClient, method: str, params: dict | None = None) -> dict | None:
        payload = {
            "jsonrpc": "2.0",
            "id": self._alloc_id(),
            "method": method,
            "params": params or {},
        }
        resp, msg = await self._post(client, payload)
        if resp.status_code >= 400:
            return {
                "error": {
                    "message": f"HTTP {resp.status_code}: {_short_err_body(resp.text)}",
                    "code": resp.status_code,
                }
            }
        if msg is None:
            return {"error": {"message": f"无法解析 MCP 响应: {_short_err_body(resp.text, 300)}"}}
        return msg


# ---------- Stdio (npx / local process) ----------


class _StdioSession:
    def __init__(self, command: str, args: list[str], env: dict[str, str]):
        self.command = command
        self.args = args
        self.env = env
        self.proc: asyncio.subprocess.Process | None = None
        self._next_id = 1
        self._buf = b""
        self._stderr_task: asyncio.Task | None = None
        self.stderr_log = ""

    def _alloc_id(self) -> int:
        i = self._next_id
        self._next_id += 1
        return i

    async def _drain_stderr(self) -> None:
        assert self.proc and self.proc.stderr
        try:
            while True:
                chunk = await self.proc.stderr.read(1024)
                if not chunk:
                    break
                self.stderr_log += chunk.decode("utf-8", errors="replace")
                if len(self.stderr_log) > 8000:
                    self.stderr_log = self.stderr_log[-4000:]
        except Exception:
            pass

    async def start(self) -> None:
        merged = {**os.environ, **{str(k): str(v) for k, v in (self.env or {}).items()}}
        # Quiet npm/npx so it does not pollute stdout (MCP uses stdout for JSON-RPC)
        merged.pop("npm_config_devdir", None)
        merged.pop("NPM_CONFIG_DEVDIR", None)
        merged.setdefault("NPM_CONFIG_LOGLEVEL", "error")
        merged.setdefault("npm_config_loglevel", "error")
        merged.setdefault("NO_UPDATE_NOTIFIER", "1")
        merged.setdefault("NPM_CONFIG_UPDATE_NOTIFIER", "false")
        for k in list(merged):
            if k.lower() in ("http_proxy", "https_proxy", "all_proxy"):
                # Local npx + MCP servers often break behind corporate proxies meant for browsers
                merged.pop(k, None)
        if os.path.sep not in self.command and shutil.which(self.command, path=merged.get("PATH")) is None:
            raise FileNotFoundError(
                f"stdio MCP 命令不存在: {self.command!r}；请把该命令安装到 API 容器 PATH，"
                "或在 MCP 配置中填写容器内可执行文件的绝对路径"
            )
        self.proc = await asyncio.create_subprocess_exec(
            self.command,
            *self.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=merged,
            limit=1024 * 1024,
        )
        self._stderr_task = asyncio.create_task(self._drain_stderr())

    async def close(self) -> None:
        if not self.proc:
            return
        try:
            if self.proc.stdin and not self.proc.stdin.is_closing():
                self.proc.stdin.close()
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=3)
            except asyncio.TimeoutError:
                self.proc.kill()
        except Exception:
            pass
        if self._stderr_task:
            self._stderr_task.cancel()
        self.proc = None

    def _encode(self, msg: dict) -> bytes:
        # Official MCP SDK StdioServerTransport uses NDJSON (JSON line + "\n"),
        # NOT LSP-style Content-Length framing.
        return (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")

    async def _read_message(self, timeout: float = 90.0) -> dict:
        assert self.proc and self.proc.stdout
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise TimeoutError(f"stdio MCP 读取超时; stderr={self.stderr_log[-400:]!r}")

            # Prefer NDJSON (current MCP SDK). Skip blank / non-JSON noise (npx banners).
            while b"\n" in self._buf:
                line, self._buf = self._buf.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                # Legacy Content-Length header line — switch to header mode
                if line.lower().startswith(b"content-length:"):
                    self._buf = line + b"\n" + self._buf
                    break
                if line.startswith(b"{"):
                    return json.loads(line.decode("utf-8"))
                # ignore non-JSON stdout noise

            # Legacy Content-Length framed (rare)
            if b"Content-Length:" in self._buf[:80] or b"content-length:" in self._buf[:80]:
                sep = b"\r\n\r\n" if b"\r\n\r\n" in self._buf else (b"\n\n" if b"\n\n" in self._buf else None)
                if sep:
                    header, rest = self._buf.split(sep, 1)
                    length = None
                    header_txt = header.decode("ascii", errors="ignore")
                    for hline in header_txt.replace("\r", "").split("\n"):
                        if hline.lower().startswith("content-length:"):
                            length = int(hline.split(":", 1)[1].strip())
                            break
                    if length is not None:
                        while len(rest) < length:
                            remaining = deadline - asyncio.get_event_loop().time()
                            if remaining <= 0:
                                raise TimeoutError(
                                    f"stdio MCP 读取 body 超时; stderr={self.stderr_log[-300:]!r}"
                                )
                            try:
                                chunk = await asyncio.wait_for(
                                    self.proc.stdout.read(4096), timeout=remaining
                                )
                            except asyncio.TimeoutError as e:
                                raise TimeoutError(
                                    f"stdio MCP 读取 body 超时; stderr={self.stderr_log[-300:]!r}"
                                ) from e
                            if not chunk:
                                raise RuntimeError(
                                    f"stdio MCP 进程在读取 body 时退出; stderr={self.stderr_log[-400:]!r}"
                                )
                            rest += chunk
                        body, self._buf = rest[:length], rest[length:]
                        return json.loads(body.decode("utf-8"))

            try:
                chunk = await asyncio.wait_for(
                    self.proc.stdout.read(4096), timeout=max(remaining, 0.1)
                )
            except asyncio.TimeoutError as e:
                raise TimeoutError(
                    f"stdio MCP 读取超时; stderr={self.stderr_log[-400:]!r}"
                ) from e
            if not chunk:
                code = self.proc.returncode
                raise RuntimeError(
                    f"stdio MCP 进程已退出 code={code}; stderr={self.stderr_log[-500:]!r}"
                )
            self._buf += chunk

    async def request(self, method: str, params: dict | None = None) -> dict:
        assert self.proc and self.proc.stdin
        msg_id = self._alloc_id()
        payload = {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params or {}}
        self.proc.stdin.write(self._encode(payload))
        await self.proc.stdin.drain()
        # First reply after cold npx start can take a while
        read_timeout = 120.0 if method == "initialize" else 90.0
        while True:
            msg = await self._read_message(timeout=read_timeout)
            # skip notifications
            if "id" not in msg:
                continue
            if msg.get("id") == msg_id:
                return msg

    async def notify(self, method: str, params: dict | None = None) -> None:
        assert self.proc and self.proc.stdin
        payload = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        self.proc.stdin.write(self._encode(payload))
        await self.proc.stdin.drain()


async def _stdio_list_tools(mcp) -> tuple[list[dict], str]:
    command = (getattr(mcp, "command", None) or "").strip()
    if not command:
        return [], "stdio 协议需要填写启动命令（如 npx）"
    args = _parse_json_obj(getattr(mcp, "command_args", None), [])
    if isinstance(getattr(mcp, "command_args", None), list):
        args = mcp.command_args
    env = _parse_json_obj(getattr(mcp, "command_env", None), {})
    if isinstance(getattr(mcp, "command_env", None), dict):
        env = mcp.command_env
    # Also accept API key style from headers for convenience
    headers = _parse_headers(getattr(mcp, "headers", None) or "{}")
    for k, v in headers.items():
        env.setdefault(str(k).replace("-", "_").upper() if False else str(k), str(v))
    # Map common header keys to getnote env
    if headers.get("Authorization") and "GETNOTE_API_KEY" not in env:
        env["GETNOTE_API_KEY"] = headers["Authorization"]
    if headers.get("X-Client-ID") and "GETNOTE_CLIENT_ID" not in env:
        env["GETNOTE_CLIENT_ID"] = headers["X-Client-ID"]

    session = _StdioSession(command, [str(a) for a in args], env)
    try:
        await session.start()
        init = await session.request(
            "initialize",
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "gap", "version": "1.0"},
            },
        )
        if "error" in init:
            return [], f"initialize 失败: {init['error']}"
        await session.notify("notifications/initialized", {})
        listed = await session.request("tools/list", {})
        if "error" in listed:
            return [], f"tools/list 失败: {listed['error']}"
        return _normalize_tools(listed), ""
    except Exception as e:
        return [], f"stdio MCP 失败: {e}"
    finally:
        await session.close()


async def _stdio_call_tool(mcp, tool: str, args: dict) -> str:
    command = (getattr(mcp, "command", None) or "").strip()
    if not command:
        return "MCP 错误: stdio 未配置 command"
    arg_list = _parse_json_obj(getattr(mcp, "command_args", None), [])
    if isinstance(getattr(mcp, "command_args", None), list):
        arg_list = mcp.command_args
    env = _parse_json_obj(getattr(mcp, "command_env", None), {})
    if isinstance(getattr(mcp, "command_env", None), dict):
        env = mcp.command_env
    headers = _parse_headers(getattr(mcp, "headers", None) or "{}")
    if headers.get("Authorization") and "GETNOTE_API_KEY" not in env:
        env["GETNOTE_API_KEY"] = headers["Authorization"]
    if headers.get("X-Client-ID") and "GETNOTE_CLIENT_ID" not in env:
        env["GETNOTE_CLIENT_ID"] = headers["X-Client-ID"]

    session = _StdioSession(command, [str(a) for a in arg_list], env)
    try:
        await session.start()
        init = await session.request(
            "initialize",
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "gap", "version": "1.0"},
            },
        )
        if "error" in init:
            return _tool_result_text(init)
        await session.notify("notifications/initialized", {})
        result = await session.request("tools/call", {"name": tool, "arguments": args or {}})
        text = _tool_result_text(result)
        return await _append_tool_catalog_hint(session, text)
    except Exception as e:
        return _format_mcp_call_failure(e)
    finally:
        await session.close()


async def _legacy_list_tools(url: str, headers: dict) -> list[dict]:
    async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
        resp = await client.get(f"{url.rstrip('/')}/tools", headers=headers)
        if resp.status_code == 200 and not _looks_like_html_404(resp.text):
            try:
                return _normalize_tools(resp.json())
            except Exception:
                return []
    return []


async def _legacy_call_tool(url: str, headers: dict, tool: str, args: dict) -> str:
    async with httpx.AsyncClient(timeout=60, trust_env=False) as client:
        resp = await client.post(
            f"{url.rstrip('/')}/call",
            headers=headers,
            json={"tool": tool, "arguments": args},
        )
        if resp.status_code == 200 and not _looks_like_html_404(resp.text):
            try:
                return json.dumps(resp.json(), ensure_ascii=False)
            except Exception:
                return resp.text
        return f"MCP 错误: HTTP {resp.status_code}: {_short_err_body(resp.text)}"


def _format_mcp_call_failure(exc: BaseException, *, url: str = "") -> str:
    """Never return a bare 'MCP 调用失败:' with empty detail."""
    name = type(exc).__name__
    detail = str(exc).strip() or repr(exc)
    msg = f"MCP 调用失败: {name}: {detail}"
    if url and isinstance(
        exc,
        (
            httpx.TimeoutException,
            httpx.ConnectError,
            httpx.RemoteProtocolError,
            httpx.NetworkError,
        ),
    ):
        # Strip query/fragment so tokens in URL query never leak.
        safe = (url or "").split("?", 1)[0].split("#", 1)[0]
        if safe:
            msg = f"{msg} url={safe}"
    return msg


def _is_transport_error(exc: BaseException) -> bool:
    return isinstance(
        exc,
        (
            httpx.TimeoutException,
            httpx.ConnectError,
            httpx.RemoteProtocolError,
            httpx.NetworkError,
            httpx.ReadError,
            httpx.WriteError,
            ConnectionError,
            TimeoutError,
            OSError,
        ),
    )


def _should_retry_transport(tool: str, exc: BaseException) -> bool:
    """Retry transport flakes for query-like tools (not business/JSON-RPC errors)."""
    if not _is_transport_error(exc):
        return False
    name = (tool or "").strip().lower()
    if name in _QUERY_LIKE_TOOLS:
        return True
    # Conservative: also retry generic unknown tool names on pure connect/timeout
    return isinstance(exc, (httpx.TimeoutException, httpx.ConnectError, TimeoutError, OSError))


async def _streamable_call_once(
    url: str,
    headers: dict,
    tool_name: str,
    args: dict,
) -> str:
    async with httpx.AsyncClient(timeout=_STREAMABLE_CALL_TIMEOUT, trust_env=False) as client:
        session = _StreamableSession(url, headers)
        init = await session.initialize(client)
        if init and "error" in init:
            return _tool_result_text(init)
        result = await session.rpc(
            client,
            "tools/call",
            {"name": tool_name, "arguments": args or {}},
        )
        text = _tool_result_text(result)
        if _looks_like_unknown_tool(text):
            listed = await session.rpc(client, "tools/list", {})
            hint = _format_available_tools(_normalize_tools(listed or {}))
            if hint:
                text = (
                    f"{text}\n{hint}\n"
                    '请改用上列真实工具名，例如: MCP: list_notes {"since_id":0}'
                )
        return text


def _streamable_empty_hint(url: str, detail: str = "") -> str:
    """Helpful error when Streamable MCP returns no tools (health ≠ MCP endpoint)."""
    from urllib.parse import urlparse

    parts = []
    detail = (detail or "").strip().rstrip("。.")
    if detail:
        parts.append(detail)
    try:
        path = (urlparse(url or "").path or "").rstrip("/")
    except Exception:
        path = ""
    if not path or path == "/":
        base = (url or "").rstrip("/")
        parts.append(
            f"当前地址是根路径，健康检查通不等于 MCP 端点；请改为 Streamable 路径，例如 `{base}/mcp`"
        )
    auth_hint = "并确认 Authorization 使用 `Bearer <token>`（若服务端要求）"
    if auth_hint not in "".join(parts):
        parts.append(auth_hint)
    return "。".join(p for p in parts if p) or "Streamable MCP 未返回工具列表"


async def connect_mcp_detail(mcp) -> dict[str, Any]:
    """Return {tools, error}."""
    protocol = (mcp.protocol or "sse").lower()

    if _is_stdio(protocol):
        tools, err = await _stdio_list_tools(mcp)
        return {"tools": tools, "error": err}

    if not mcp.url:
        return {"tools": [], "error": "未配置 MCP URL（远程 SSE）或未选择 stdio 命令"}

    headers = _parse_headers(mcp.headers)

    if _is_streamable(protocol):
        try:
            async with httpx.AsyncClient(timeout=_STREAMABLE_LIST_TIMEOUT, trust_env=False) as client:
                session = _StreamableSession(mcp.url, headers)
                init = await session.initialize(client)
                if init and "error" in init:
                    err = str(init["error"].get("message") or init["error"])
                    return {"tools": [], "error": _streamable_empty_hint(mcp.url, err)}
                listed = await session.rpc(client, "tools/list", {})
                tools = _normalize_tools(listed or {})
                if tools:
                    return {"tools": tools, "error": ""}
                if listed and "error" in listed:
                    err = str(listed["error"].get("message") or listed["error"])
                    return {"tools": [], "error": _streamable_empty_hint(mcp.url, err)}
                return {
                    "tools": [],
                    "error": _streamable_empty_hint(
                        mcp.url,
                        "tools/list 无结果。若是得到大脑，请改用协议 stdio + 命令 npx -y @getnote/mcp",
                    ),
                }
        except Exception as e:
            msg = str(e).strip() or type(e).__name__
            return {"tools": [], "error": _streamable_empty_hint(mcp.url, f"Streamable 连接失败: {msg}")}

    if protocol in ("http", "rest", "legacy"):
        try:
            tools = await _legacy_list_tools(mcp.url, headers)
            if tools:
                return {"tools": tools, "error": ""}
            return {"tools": [], "error": "HTTP 简易协议未返回工具列表"}
        except Exception as e:
            return {"tools": [], "error": f"HTTP 简易协议失败: {e}"}

    return {"tools": [], "error": f"不支持的协议: {protocol}"}


async def call_mcp_tool(mcp, tool: str, args: dict) -> str:
    protocol = (mcp.protocol or "sse").lower()
    tool_name = (tool or "").strip()
    if not tool_name:
        return "MCP 错误: 未指定工具名"

    if _is_stdio(protocol):
        return await _stdio_call_tool(mcp, tool_name, args or {})

    if not mcp.url:
        return "MCP URL 未配置"
    headers = _parse_headers(mcp.headers)

    if _is_streamable(protocol):
        last_exc: BaseException | None = None
        # Query-like tools get transport retries; others fail on first error.
        max_attempts = (
            _TRANSPORT_RETRY_MAX
            if (tool_name or "").strip().lower() in _QUERY_LIKE_TOOLS
            else 1
        )
        for attempt in range(max_attempts):
            try:
                return await _streamable_call_once(
                    mcp.url, headers, tool_name, args or {},
                )
            except Exception as e:
                last_exc = e
                can_retry = (
                    attempt + 1 < max_attempts
                    and _should_retry_transport(tool_name, e)
                )
                logger.warning(
                    "streamable MCP call failed tool=%s attempt=%s/%s retry=%s err=%s",
                    tool_name,
                    attempt + 1,
                    max_attempts,
                    can_retry,
                    _format_mcp_call_failure(e, url=mcp.url),
                )
                if not can_retry:
                    return _format_mcp_call_failure(e, url=mcp.url)
                delay = _TRANSPORT_RETRY_BACKOFF_S[
                    min(attempt, len(_TRANSPORT_RETRY_BACKOFF_S) - 1)
                ]
                await asyncio.sleep(delay)
        if last_exc is not None:
            return _format_mcp_call_failure(last_exc, url=mcp.url)
        return "MCP 调用失败: RuntimeError: streamable call exhausted"

    if protocol in ("http", "rest", "legacy"):
        try:
            return await _legacy_call_tool(mcp.url, headers, tool_name, args or {})
        except Exception as e:
            return _format_mcp_call_failure(e, url=mcp.url)

    return "MCP 调用失败: ValueError: 不支持的协议类型"


# ---------- Per-run session manager (reuse + failure recovery) ----------


class _SessionBroken(Exception):
    """Cached MCP session is dead (stdio process exited / streamable transport or session error)."""


@dataclass
class _MCPHandle:
    """One cached MCP session, discriminated by protocol."""

    protocol: str  # "stdio" | "streamable"
    stdio: _StdioSession | None = None
    streamable: _StreamableSession | None = None
    error: str = ""  # non-empty → handshake failed with a business error; surface it


def _stdio_config(mcp) -> tuple[str, list[str], dict]:
    """Extract (command, args, env) from an MCP row for stdio sessions."""
    command = (getattr(mcp, "command", None) or "").strip()
    arg_list = _parse_json_obj(getattr(mcp, "command_args", None), [])
    if isinstance(getattr(mcp, "command_args", None), list):
        arg_list = mcp.command_args
    env = _parse_json_obj(getattr(mcp, "command_env", None), {})
    if isinstance(getattr(mcp, "command_env", None), dict):
        env = mcp.command_env
    headers = _parse_headers(getattr(mcp, "headers", None) or "{}")
    for k, v in headers.items():
        env.setdefault(str(k), str(v))
    if headers.get("Authorization") and "GETNOTE_API_KEY" not in env:
        env["GETNOTE_API_KEY"] = headers["Authorization"]
    if headers.get("X-Client-ID") and "GETNOTE_CLIENT_ID" not in env:
        env["GETNOTE_CLIENT_ID"] = headers["X-Client-ID"]
    return command, [str(a) for a in arg_list], env


def _json_keys_summary(text: str, *, max_keys: int = 24, max_chars: int = 320) -> str:
    """Compact key/field summary of a JSON payload for the oversized-result notice.

    Lets the model record a view→field mapping without READ-ing the full materialized
    file. Returns "" for non-JSON or empty payloads (caller drops the line).
    """
    try:
        data = json.loads(text)
    except Exception:
        return ""
    parts: list[str] = []
    if isinstance(data, dict):
        top = list(data.keys())
        parts.extend(str(k) for k in top[:max_keys])
        for k in top[:3]:
            v = data.get(k)
            if isinstance(v, list) and v and isinstance(v[0], dict):
                fields = [str(f) for f in v[0].keys()][:max_keys]
                parts.append(f"{k}[]: {', '.join(fields)}")
    elif isinstance(data, list):
        if not data:
            return ""
        parts.append(f"[{len(data)} 项]")
        if isinstance(data[0], dict):
            parts.extend(str(f) for f in list(data[0].keys())[:max_keys])
    if not parts:
        return ""
    summary = ", ".join(parts)
    if len(summary) > max_chars:
        summary = summary[: max_chars - 1] + "…"
    return f"结构摘要: {summary}"


def _result_shape(text: str) -> str:
    """Compact element/row count for a JSON payload (D13).

    Lets the model judge whether a result is empty/incomplete without READ-ing the
    materialized file. Returns "" for non-JSON or empty payloads.
    """
    try:
        data = json.loads(text)
    except Exception:
        return ""
    if isinstance(data, list):
        return f"[{len(data)} 项]"
    if isinstance(data, dict):
        lists = [len(v) for v in data.values() if isinstance(v, list)]
        if lists:
            return f"[{max(lists)} 行]"
        return f"[{len(data)} 键]"
    return ""


_SQL_KEYWORD_RE = re.compile(
    r"\b(select|with|show|desc|describe|explain|insert|update|delete|merge|create|drop|alter|truncate|call)\b",
    re.I,
)


def _normalize_sql(sql: str) -> str:
    """Normalize a SQL string for dedup (react-engine-v16 R4).

    Collapses whitespace, lowercases, and strips trailing semicolons so the same
    query with different formatting/case/punctuation shares one key. LIMIT/OFFSET
    values are preserved (never stripped), so a legitimately different pagination
    stays a different key.
    """
    s = (sql or "").strip()
    s = s.rstrip(";").strip()
    s = " ".join(s.split())
    return s.lower()


def _normalize_ads_sql_args(args: dict) -> dict:
    """Copy ``args``, SQL-normalizing any SQL-looking string value.

    Only ``execute_ads_sql`` uses this: its payload is the SQL text, so
    whitespace/case/trailing-semicolon variants collapse to one dedup key. Non-SQL
    string values (e.g. a database name) are left untouched.
    """
    if not isinstance(args, dict):
        return args
    out: dict = {}
    for k, v in args.items():
        if isinstance(v, str) and _SQL_KEYWORD_RE.search(v):
            out[k] = _normalize_sql(v)
        else:
            out[k] = v
    return out


class McpSessionManager:
    """Per-run cache of MCP sessions keyed by ``mcp.id``.

    Reuses one stdio subprocess / streamable HTTP session across every tool call
    in a run instead of handshaking + respawning per call. Legacy ``http``/``rest``
    is stateless and never cached.

    On a session-level failure (stdio process exit, streamable transport/session
    error) the dead session is dropped, rebuilt once, and the single call retried
    — layered on top of the per-call transport retry already in ``call_mcp_tool``.

    Observability: ``events`` records every create/reuse/rebuild, and each is also
    emitted via ``logger.info`` so the runtime can surface or audit them.
    """

    def __init__(
        self,
        *,
        query_cache: dict | None = None,
        mcp_results: list | None = None,
        run_ts: str = "",
        sandbox=None,
        large_result_chars: int = 6000,
        max_cache_entries: int = 100,
        max_cached_result_chars: int = 2_000_000,
        max_mcp_results: int = 100,
    ) -> None:
        self._sessions: dict[str, _MCPHandle] = {}
        self._client: httpx.AsyncClient | None = None
        self._closed = False
        self.events: list[dict] = []
        # Query dedup + oversized-result materialization (wired in by the runtime).
        self.query_cache: dict = query_cache if query_cache is not None else {}
        self.mcp_results: list = mcp_results if mcp_results is not None else []
        self.run_ts: str = run_ts
        self.sandbox = sandbox
        self.large_result_chars = large_result_chars
        # Cross-run query_cache bounds (D7): keep the persisted AgentRunState.state
        # blob from growing without limit. LRU by entry count + per-entry size cap.
        self.max_cache_entries = max_cache_entries
        self.max_cached_result_chars = max_cached_result_chars
        # Bounds the persisted mcp_results list (D6): keep the most recent N, mirroring
        # the query_cache LRU. A monotonic seq (not len(list)) avoids file-name reuse
        # after trimming, so a resumed run never overwrites a previously-written result.
        self.max_mcp_results = max_mcp_results
        self._next_mcp_seq = max((int(r.get("seq", 0)) for r in self.mcp_results), default=-1) + 1

    async def __aenter__(self) -> "McpSessionManager":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    def _observe(self, event: str, mcp_id: str, tool: str = "", detail: str = "") -> None:
        self.events.append(
            {"event": event, "mcp_id": mcp_id, "tool": tool, "detail": detail}
        )
        logger.info("mcp_session %s mcp=%s tool=%s %s", event, mcp_id, tool, detail)

    def _key(self, mcp) -> str:
        return str(getattr(mcp, "id", None) or "")

    def _client_for_streamable(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=_STREAMABLE_CALL_TIMEOUT, trust_env=False
            )
        return self._client

    async def call_tool(self, mcp, tool: str, args: dict) -> str:
        protocol = (mcp.protocol or "sse").lower()
        tool_name = (tool or "").strip()
        if not tool_name:
            return "MCP 错误: 未指定工具名"

        # Query dedup: identical (mcp, tool, args) already materialized → return a
        # reference instead of re-calling + re-writing the result file.
        key = self._dedup_key(mcp, tool_name, args)
        cached = self.query_cache.get(key)
        if cached:
            self._observe("dedup_hit", self._key(mcp), tool_name, str(cached.get("path", "")))
            return self._cached_reference(cached)

        # Legacy http/rest is stateless: direct call, never session-cached.
        if protocol in ("http", "rest", "legacy"):
            headers = _parse_headers(mcp.headers)
            if not mcp.url:
                text = "MCP URL 未配置"
            else:
                try:
                    text = await _legacy_call_tool(mcp.url, headers, tool_name, args or {})
                except Exception as e:
                    text = _format_mcp_call_failure(e, url=mcp.url)
        else:
            text = await self._call_with_session(mcp, protocol, tool_name, args or {})

        # Materialize EVERY non-empty result to disk + query_cache as a survival /
        # dedup side effect (D10/R1): the >6000 gate is gone. Only oversized results
        # are replaced by a "written to file" reference; small results stay inline
        # so the model can read them directly (tool_result_clip still clips context).
        if text:
            reference = self._materialize(mcp, tool_name, args, text, key)
            if len(text) > self.large_result_chars:
                return reference
        return text

    async def _call_with_session(self, mcp, protocol: str, tool_name: str, args: dict) -> str:
        key = self._key(mcp)
        try:
            handle = self._sessions.get(key)
            if handle is None:
                handle = await self._create(mcp, protocol)
                self._sessions[key] = handle
                self._observe("create", key, tool_name)
            else:
                self._observe("reuse", key, tool_name)
            try:
                return await self._call(handle, tool_name, args or {})
            except _SessionBroken as exc:
                # Dead session → drop, rebuild once, retry the single call.
                self._observe("rebuild", key, tool_name, str(exc)[:200])
                await self._close_one(handle)
                self._sessions.pop(key, None)
                handle = await self._create(mcp, protocol)
                self._sessions[key] = handle
                return await self._call(handle, tool_name, args or {})
        except _SessionBroken as exc:
            # Rebuild also failed (or spawn failed up front) → surface as failure text.
            return _format_mcp_call_failure(exc)

    def _dedup_key(self, mcp, tool: str, args: dict) -> str:
        # Dedup is MCP-only by design (D11). The `query_cache` signature interface
        # (`_dedup_key` + `_cached_reference`) is deliberately generic so RAG/httpmcp
        # can adopt it later without touching the runtime; not implemented for them now.
        mid = str(getattr(mcp, "id", None) or "")
        if tool == "execute_ads_sql":
            # react-engine-v16 R4: SQL 归一化特例 — 去空白/大小写/尾分号，LIMIT/OFFSET 不同仍视为不同。
            args = _normalize_ads_sql_args(args)
        try:
            norm = json.dumps(args or {}, sort_keys=True, ensure_ascii=False, default=str)
        except Exception:
            norm = repr(args)
        return f"{mid}\x00{tool}\x00{norm}"

    def _trim_query_cache(self) -> None:
        """LRU by entry count: drop the oldest-inserted keys past the cap.

        ``dict`` preserves insertion order, so ``next(iter(...))`` is the oldest
        entry. Bounds the persisted ``query_cache`` (cross-run checkpoint blob)
        without a hard gate on the loop (D7, memory hygiene only).
        """
        while len(self.query_cache) > self.max_cache_entries:
            oldest = next(iter(self.query_cache))
            del self.query_cache[oldest]

    def _trim_mcp_results(self) -> None:
        """Keep only the most recent ``max_mcp_results`` entries (D6).

        Dropped entries leave their result files on disk; only the in-memory
        manifest (persisted into AgentRunState.state) is bounded.
        """
        while len(self.mcp_results) > self.max_mcp_results:
            self.mcp_results.pop(0)

    def _cached_reference(self, entry: dict) -> str:
        path = entry.get("path", "")
        size = entry.get("size", 0)
        shape = entry.get("shape", "")
        shape_hint = f"，{shape}" if shape else ""
        return (
            f"该结果已缓存/已落盘 {path}（{size} 字符{shape_hint}）。"
            "请 READ 取回该文件，勿重跑；或用 SHELL 继续处理。"
        )

    def _materialize(self, mcp, tool: str, args: dict, text: str, key: str) -> str:
        from app.services.workplace import workplace_root

        sid = self.sandbox.id if self.sandbox else "default"
        seq = self._next_mcp_seq
        self._next_mcp_seq += 1
        rel = f"task/{self.run_ts}/mcp_result_{seq}.json"
        try:
            wp = workplace_root(sid) / rel
            wp.parent.mkdir(parents=True, exist_ok=True)
            wp.write_text(text, encoding="utf-8")
        except Exception:
            return text
        shape = _result_shape(text)
        self.mcp_results.append(
            {"seq": seq, "path": rel, "tool": tool, "args": args, "size": len(text), "shape": shape}
        )
        self._trim_mcp_results()
        if len(text) <= self.max_cached_result_chars:
            self.query_cache[key] = {"path": rel, "tool": tool, "size": len(text), "shape": shape}
            self._trim_query_cache()
        self._observe("materialize", self._key(mcp), tool, rel)
        keys_summary = _json_keys_summary(text)
        summary_line = f"{keys_summary}\n" if keys_summary else ""
        shape_hint = f"，{shape}" if shape else ""
        return (
            f"MCP 结果过大（{len(text)} 字符{shape_hint}），已全量写入 {rel}。\n"
            f"{summary_line}"
            "请勿在本轮上下文粘贴原始数据；用 READ 查看结构，"
            "或用 SHELL + pandas/openpyxl 读取该文件继续处理（如生成 xlsx）。"
        )

    async def _create(self, mcp, protocol: str) -> _MCPHandle:
        if _is_stdio(protocol):
            command, arg_list, env = _stdio_config(mcp)
            if not command:
                return _MCPHandle("stdio", error="MCP 错误: stdio 未配置 command")
            sess = _StdioSession(command, arg_list, env)
            try:
                await sess.start()
            except Exception as e:
                raise _SessionBroken(f"stdio 启动失败: {e}") from e
            try:
                init = await sess.request(
                    "initialize",
                    {
                        "protocolVersion": MCP_PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "gap", "version": "1.0"},
                    },
                )
            except (RuntimeError, TimeoutError) as e:
                await sess.close()
                raise _SessionBroken(f"stdio initialize 失败: {e}") from e
            if "error" in init:
                await sess.close()
                return _MCPHandle("stdio", error=_tool_result_text(init))
            await sess.notify("notifications/initialized", {})
            return _MCPHandle("stdio", stdio=sess)

        # streamable
        client = self._client_for_streamable()
        sess = _StreamableSession(mcp.url, _parse_headers(mcp.headers))
        try:
            init = await sess.initialize(client)
        except Exception as e:
            raise _SessionBroken(f"streamable initialize 失败: {e}") from e
        if init and "error" in init:
            return _MCPHandle("streamable", error=_tool_result_text(init))
        return _MCPHandle("streamable", streamable=sess)

    async def _call(self, handle: _MCPHandle, tool: str, args: dict) -> str:
        if handle.error:
            return handle.error
        if handle.protocol == "stdio":
            return await self._call_stdio(handle.stdio, tool, args)
        return await self._call_streamable(handle.streamable, tool, args)

    async def _call_stdio(self, sess: _StdioSession, tool: str, args: dict) -> str:
        try:
            result = await sess.request("tools/call", {"name": tool, "arguments": args or {}})
        except (RuntimeError, TimeoutError) as e:
            raise _SessionBroken(f"stdio 进程异常: {e}") from e
        except Exception as e:
            return _format_mcp_call_failure(e)
        text = _tool_result_text(result)
        return await _append_tool_catalog_hint(sess, text)

    async def _streamable_rpc_once(
        self, sess: _StreamableSession, client: httpx.AsyncClient, tool: str, args: dict
    ) -> str:
        result = await sess.rpc(client, "tools/call", {"name": tool, "arguments": args or {}})
        text = _tool_result_text(result)
        if _looks_like_unknown_tool(text):
            listed = await sess.rpc(client, "tools/list", {})
            hint = _format_available_tools(_normalize_tools(listed or {}))
            if hint:
                text = (
                    f"{text}\n{hint}\n"
                    '请改用上列真实工具名，例如: MCP: list_notes {"since_id":0}'
                )
        return text

    async def _call_streamable(self, sess: _StreamableSession, tool: str, args: dict) -> str:
        client = self._client_for_streamable()
        max_attempts = (
            _TRANSPORT_RETRY_MAX
            if (tool or "").strip().lower() in _QUERY_LIKE_TOOLS
            else 1
        )
        last_exc: BaseException | None = None
        for attempt in range(max_attempts):
            try:
                return await self._streamable_rpc_once(sess, client, tool, args or {})
            except Exception as e:
                last_exc = e
                can_retry = attempt + 1 < max_attempts and _should_retry_transport(tool, e)
                logger.warning(
                    "mcp_session streamable call failed tool=%s attempt=%s/%s retry=%s err=%s",
                    tool,
                    attempt + 1,
                    max_attempts,
                    can_retry,
                    _format_mcp_call_failure(e, url=sess.url),
                )
                if can_retry:
                    delay = _TRANSPORT_RETRY_BACKOFF_S[
                        min(attempt, len(_TRANSPORT_RETRY_BACKOFF_S) - 1)
                    ]
                    await asyncio.sleep(delay)
                    continue
                if _is_transport_error(e):
                    raise _SessionBroken(f"streamable 连接失败: {e}") from e
                return _format_mcp_call_failure(e, url=sess.url)
        raise _SessionBroken(str(last_exc or "streamable call exhausted"))

    async def _close_one(self, handle: _MCPHandle) -> None:
        if handle.stdio:
            await handle.stdio.close()
        # streamable session has no persistent transport of its own (shared client
        # is closed in close()); nothing else to do here.

    async def close(self) -> None:
        """Close every cached session, swallowing per-session close errors.

        A single misbehaving subprocess must not mask the run's real outcome (task 1.2).
        """
        if self._closed:
            return
        self._closed = True
        for key, handle in list(self._sessions.items()):
            try:
                await self._close_one(handle)
            except Exception:
                logger.warning("mcp_session close failed mcp=%s", key, exc_info=True)
        self._sessions.clear()
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._client = None
