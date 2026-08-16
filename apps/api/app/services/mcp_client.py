"""MCP Streamable HTTP / stdio / legacy HTTP client."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
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


async def connect_mcp(mcp) -> list[dict]:
    return (await connect_mcp_detail(mcp)).get("tools") or []


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
