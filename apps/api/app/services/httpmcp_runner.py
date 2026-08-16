import json
import re
from urllib.parse import urlencode

import httpx
from jinja2 import Template

from app.models import HttpMcp
from app.security import new_id


def render_template(template: str, variables: dict) -> str:
    if not template:
        return json.dumps(variables)
    try:
        return Template(template).render(**variables)
    except Exception:
        return re.sub(r"\{\{\s*(\w+)\s*\}\}", lambda m: str(variables.get(m.group(1), "")), template)


def _pick_tool(hm: HttpMcp, variables: dict | None) -> dict | None:
    tools = hm._tools()
    if not tools:
        return None
    vars_ = variables or {}
    name = (vars_.get("tool") or vars_.get("_tool") or "").strip()
    if name:
        for t in tools:
            if t.get("name") == name or t.get("id") == name:
                return t
    return tools[0]


def _headers_from_pairs(pairs: list | None) -> dict:
    out = {}
    for item in pairs or []:
        if isinstance(item, dict):
            k = (item.get("key") or "").strip()
            if k:
                out[k] = str(item.get("value") or "")
    return out


def _merge_auth_headers(hm: HttpMcp, headers: dict) -> dict:
    auth = []
    try:
        auth = json.loads(hm.auth_json or "[]")
    except Exception:
        auth = []
    for item in auth or []:
        if not isinstance(item, dict):
            continue
        k = (item.get("key") or "").strip()
        if k and k not in headers:
            headers[k] = str(item.get("value") or "")
    return headers


def _apply_fixed_args(tool: dict, variables: dict) -> dict:
    merged = dict(variables or {})
    for item in tool.get("fixed_args") or []:
        if isinstance(item, dict):
            k = (item.get("key") or "").strip()
            if k:
                merged[k] = item.get("value")
    merged.pop("tool", None)
    merged.pop("_tool", None)
    return merged


async def call_httpmcp(hm: HttpMcp, variables: dict | None = None) -> str:
    tool = _pick_tool(hm, variables)
    if tool:
        return await _call_tool(hm, tool, variables or {})

    # Legacy flat endpoint
    headers = json.loads(hm.headers or "{}")
    headers = _merge_auth_headers(hm, headers)
    body_str = render_template(hm.body_template or "{}", variables or {})
    try:
        body = json.loads(body_str)
    except Exception:
        body = body_str
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.request(
            hm.method or "POST",
            hm.url,
            headers=headers,
            json=body if isinstance(body, dict) else None,
            content=body if isinstance(body, str) else None,
        )
        return json.dumps({"status": resp.status_code, "body": resp.text[:8000]}, ensure_ascii=False)


async def _call_tool(hm: HttpMcp, tool: dict, variables: dict) -> str:
    method = (tool.get("method") or "GET").upper()
    url = (tool.get("url") or "").strip()
    if not url:
        return json.dumps({"error": "tool url empty"}, ensure_ascii=False)

    headers = _headers_from_pairs(tool.get("headers"))
    headers = _merge_auth_headers(hm, headers)
    params = _apply_fixed_args(tool, variables)
    timeout = float(tool.get("timeout") or 300)

    body_template = tool.get("body_template") or ""
    json_body = None
    content = None
    if method in ("POST", "PUT", "PATCH"):
        if body_template:
            body_str = render_template(body_template, params)
            try:
                json_body = json.loads(body_str)
            except Exception:
                content = body_str
        else:
            json_body = params
    else:
        # GET/DELETE: query string from params
        if params:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}{urlencode({k: str(v) for k, v in params.items()})}"

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.request(
            method,
            url,
            headers=headers or None,
            json=json_body,
            content=content,
        )
        return json.dumps({"status": resp.status_code, "body": resp.text[:8000]}, ensure_ascii=False)


def sync_legacy_fields(hm: HttpMcp, tools: list) -> None:
    """Keep url/method/headers in sync with first tool for older callers."""
    if not tools:
        hm.url = hm.url or ""
        return
    t0 = tools[0]
    hm.url = t0.get("url") or ""
    hm.method = t0.get("method") or "GET"
    hdr = _headers_from_pairs(t0.get("headers"))
    hm.headers = json.dumps(hdr, ensure_ascii=False)
    hm.body_template = t0.get("body_template") or ""


def normalize_tools(tools: list | None) -> list:
    out = []
    for t in tools or []:
        if not isinstance(t, dict):
            continue
        out.append(
            {
                "id": t.get("id") or new_id(),
                "name": (t.get("name") or "未命名").strip(),
                "method": (t.get("method") or "GET").upper(),
                "url": (t.get("url") or "").strip(),
                "description": t.get("description") or "",
                "timeout": int(t.get("timeout") or 300),
                "headers": t.get("headers") if isinstance(t.get("headers"), list) else [],
                "fixed_args": t.get("fixed_args") if isinstance(t.get("fixed_args"), list) else [],
                "args": t.get("args") if isinstance(t.get("args"), list) else [],
                "body_template": t.get("body_template") or "",
            }
        )
    return out
