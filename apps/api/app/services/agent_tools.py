import asyncio
import json
import re
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import Skill, MCP, Sandbox, RagCorpus, HttpMcp
from app.services import docker_service, skill_runtime
from app.services.mcp_client import call_mcp_tool
from app.services.agent_runtime.utils import (
    _get_mcp_tools_cached,
    normalize_mcp_tool_args,
    record_ads_view_catalog,
)
from app.services.httpmcp_runner import call_httpmcp
from app.services.rag_indexer import search_corpus
from app.services.workplace import format_dir_listing, find_files, workplace_root, benchmark_workplace_root

BINARY_EXTS = {".xlsx", ".xlsm", ".xls", ".pdf", ".zip", ".png", ".jpg", ".jpeg", ".gif", ".webp"}
# Shell metacharacters / junk that must never become a workplace path segment
_UNSAFE_PATH_RE = re.compile(r"[|&;<>`$()\n\r]|/{2,}")
_SHELL_TAIL_RE = re.compile(r"(?:2>&1|2>|1>|>>|[|;&<>]|&&|\|\|)")
# Writing .py under task/ triggers uvicorn --reload and kills long ReAct runs
_TASK_PY_REL_RE = re.compile(r"(?:^|/)task/(?:[^/\s]+/)*[^/\s]+\.py$", re.I)
_SHELL_TASK_PY_WRITE_RE = re.compile(
    r"(?:"
    r"(?:tee|cat\s*>|>>)\s+[^\s;|&]*task/[^\s;|&]*\.py\b"
    r"|(?:open|Path)\s*\(\s*['\"][^'\"]*task/[^'\"]*\.py['\"]"
    r"|\btask/[^\s\"']+\.py\b[^\n]{0,80}\b(?:write|writelines|dump)\b"
    r"|['\"]task/[^'\"]+\.py['\"]"
    r")",
    re.I,
)


def _extract_json_object(text: str) -> dict | None:
    """抽取文本中第一个平衡的 JSON 对象（处理多行/尾随散文）。"""
    start = (text or "").find("{")
    if start < 0:
        return None
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except Exception:
                    return None
    return None


async def execute_action(
    action: str,
    reply: str,
    db: Session,
    agent,
    sandbox: Sandbox | None,
    skill_ids: list[str],
    mcp_ids: list[str],
    rag_ids: list[str] | None = None,
    httpmcp_ids: list[str] | None = None,
    mcp_sessions=None,
) -> str:
    reply = reply.strip()

    if action == "shell" or reply.startswith("SHELL:"):
        try:
            allowed_actions = set(json.loads(agent.allowed_actions or "[]"))
        except (TypeError, ValueError, json.JSONDecodeError):
            allowed_actions = set()
        if "shell" not in allowed_actions:
            return "[permission_denied] Shell 已禁用，命令未执行。"
        cmd = reply[6:].strip() if reply.startswith("SHELL:") else reply
        # Run docker exec off the event loop so WS/step updates keep flowing
        return await asyncio.to_thread(_exec_shell, sandbox, cmd, agent.shell_timeout)

    if action == "file_read" or reply.startswith("READ:"):
        rel = reply[5:].strip().lstrip("/") if reply.startswith("READ:") else ""
        return _read_workplace(sandbox, rel)

    if action == "file_write" or reply.startswith("WRITE:"):
        if not reply.startswith("WRITE:"):
            return "use WRITE: path\\ncontent"
        parts = reply[6:].strip().split("\n", 1)
        rel = parts[0].strip().lstrip("/")
        content = parts[1] if len(parts) > 1 else ""
        return _write_workplace(sandbox, rel, content)

    if action == "file_search_replace" or reply.startswith("PATCH:"):
        if not reply.startswith("PATCH:"):
            return "use PATCH: path\\nold\\nnew"
        parts = reply[6:].strip().split("\n", 2)
        if len(parts) < 3:
            return "PATCH needs path, old, new"
        rel, old, new = parts[0].strip(), parts[1], parts[2]
        text = _read_workplace(sandbox, rel.lstrip("/"))
        if text.startswith("文件不存在"):
            return text
        if text.startswith("["):
            return text
        return _write_workplace(sandbox, rel.lstrip("/"), text.replace(old, new, 1))

    if action == "skill_read_md" or reply.startswith("SKILL_MD:"):
        sid = reply[8:].strip() if reply.startswith("SKILL_MD:") else (skill_ids[0] if skill_ids else "")
        sk = db.query(Skill).filter(Skill.id == sid).first()
        return skill_runtime.read_md(sk) if sk else "skill not found"

    if action == "skill_run_script" or reply.startswith("RUN_SKILL:"):
        name = reply[10:].strip() if reply.startswith("RUN_SKILL:") else reply
        for sid in skill_ids:
            sk = db.query(Skill).filter(Skill.id == sid).first()
            if sk and (sk.name in name or sid in name):
                cid = sandbox.container_id if sandbox else ""
                skill_timeout = int(getattr(agent, "skill_timeout", None) or 1800)
                return skill_runtime.run_script(
                    sk, sandbox.id if sandbox else "", name, cid, timeout=skill_timeout,
                )
        return "skill not matched"

    if action == "mcp_tool_call" or reply.startswith("MCP:"):
        m = re.match(r"MCP:\s*(\S+)\s*(.*)", reply, re.DOTALL)
        tool = (m.group(1) if m else "default_tool").strip().strip("`'\"")
        args = {}
        if m and m.group(2).strip():
            raw_args = m.group(2).strip()
            try:
                args = json.loads(raw_args)
            except Exception:
                args = _extract_json_object(raw_args) or {"input": raw_args}
        bound = [db.query(MCP).filter(MCP.id == mid).first() for mid in mcp_ids]
        bound = [mcp for mcp in bound if mcp]
        if not bound:
            return "no mcp configured"
        target = None
        target_tool = None
        for mcp in bound:
            tools, _ = await _get_mcp_tools_cached(mcp)
            matched = next(
                (t for t in tools if isinstance(t, dict) and str(t.get("name")) == tool),
                None,
            )
            if matched is not None:
                target = mcp
                target_tool = matched
                break
        if target is None:
            return f"未在绑定 MCP 中找到工具 {tool}"
        args = normalize_mcp_tool_args(target_tool or {}, args)
        if mcp_sessions is not None:
            result = await mcp_sessions.call_tool(target, tool, args)
        else:
            result = await call_mcp_tool(target, tool, args)
        # react-engine-v14 R3: cache list_ads_views view names / describe field
        # summaries keyed on MCP id for cross-session task_context injection.
        record_ads_view_catalog(target.id or "", tool, args, result)
        return result

    if action == "rag_query" or reply.startswith("RAG:"):
        query = reply[4:].strip() if reply.startswith("RAG:") else reply
        bound = list(rag_ids or [])
        if bound:
            existing = {
                r.id for r in db.query(RagCorpus).filter(RagCorpus.id.in_(bound)).all()
            }
            bound = [i for i in bound if i in existing]
        results = search_corpus(db, query, bound, top_k=5)
        if not results:
            return "无相关结果"
        return json.dumps(results, ensure_ascii=False)

    if action == "httpmcp_call" or reply.startswith("HTTPMCP:"):
        m = re.match(r"HTTPMCP:\s*(\S+)\s*(.*)", reply, re.DOTALL)
        tool = (m.group(1) if m else "").strip().strip("`'\"")
        args: dict = {}
        if m and m.group(2).strip():
            raw_args = m.group(2).strip()
            try:
                parsed = json.loads(raw_args)
            except Exception:
                parsed = _extract_json_object(raw_args)
            if isinstance(parsed, dict):
                args = parsed
        bound = [db.query(HttpMcp).filter(HttpMcp.id == hid).first() for hid in (httpmcp_ids or [])]
        bound = [h for h in bound if h]
        if not bound:
            return "no http mcp configured"
        target = None
        for hm in bound:
            if tool and any(
                str(t.get("name")) == tool or str(t.get("id")) == tool for t in (hm._tools() or [])
            ):
                target = hm
                break
        if target is None:
            if tool:
                return f"未在绑定 HttpMcp 中找到工具 {tool}"
            target = bound[0]
        return await call_httpmcp(target, {**args, "tool": tool} if tool else args)

    if action == "file_search" or reply.startswith("SEARCH:"):
        query = reply[7:].strip() if reply.startswith("SEARCH:") else reply
        return _search_workplace(sandbox, query)

    if reply.startswith("FINAL:"):
        return reply[6:].strip()

    return ""


def _sandbox_id(sandbox: Sandbox | None) -> str:
    return sandbox.id if sandbox else "default"


def _normalize_workplace_rel(path: str) -> str:
    """Strip absolute/redundant workplace prefixes so paths never nest as workplace/workplace/."""
    p = (path or "").strip().replace("\\", "/")
    while True:
        if p == "/workplace" or p == "workplace":
            return ""
        if p.startswith("/workplace/"):
            p = p[len("/workplace/") :].lstrip("/")
            continue
        if p.startswith("workplace/"):
            p = p[len("workplace/") :].lstrip("/")
            continue
        break
    return p.strip("/")


def _is_safe_workplace_rel(rel: str) -> bool:
    """Reject paths that look like shell fragments (e.g. 'nodes/ 2>&1 | head -50')."""
    if not rel:
        return True
    if _UNSAFE_PATH_RE.search(rel):
        return False
    for part in rel.split("/"):
        if not part or part in (".", ".."):
            return False
        # Spaces + redirect-ish tokens are almost always command junk glued onto a path
        if " " in part and any(tok in part for tok in ("echo", "head", "ls")):
            return False
    return True


def _norm_cmd(cmd: str) -> str:
    """Collapse whitespace so semantically identical shell commands share a dedup key."""
    return " ".join((cmd or "").split())


def read_cache_key(rel: str) -> str:
    """Canonical READ dedup key from a workplace-relative path (react-engine-v10 R3)."""
    return f"read\x00{_normalize_workplace_rel(rel)}"


def dedup_descriptor(
    action: str,
    reply: str,
    sandbox: Sandbox | None = None,
) -> dict | None:
    """Dedup metadata for a READ/SHELL/SEARCH call (react-engine-v10 R2/R3).

    Returns ``None`` for non-dedup-able actions. Otherwise::

        {'key': '<kind>\\x00<target>', 'rel': str|None, 'mtime': float|None}

    ``rel``/``mtime`` are only populated for READ (the target file's mtime, or
    ``None`` when the target isn't a regular file). SHELL/SEARCH keys carry no
    fingerprint — their dedup is by command / query only.
    """
    reply = (reply or "").strip()
    if action == "file_read" or reply.startswith("READ:"):
        rel = reply[5:].strip().lstrip("/") if reply.startswith("READ:") else reply
        rel = _normalize_workplace_rel(rel)
        mtime = None
        try:
            wp = workplace_root(_sandbox_id(sandbox)) / rel
            if wp.is_file():
                mtime = wp.stat().st_mtime
        except OSError:
            mtime = None
        return {"key": read_cache_key(rel), "rel": rel, "mtime": mtime}
    if action == "shell" or reply.startswith("SHELL:"):
        cmd = reply[6:].strip() if reply.startswith("SHELL:") else reply
        return {"key": f"shell\x00{_norm_cmd(cmd)}", "rel": None, "mtime": None}
    if action == "file_search" or reply.startswith("SEARCH:"):
        query = reply[7:].strip() if reply.startswith("SEARCH:") else reply
        return {"key": f"search\x00{query}", "rel": None, "mtime": None}
    return None


def _strip_shell_tail(cmd: str) -> str:
    """Cut trailing redirections / pipes / chains: `ls /workplace/x 2>&1 | head` → `ls /workplace/x`."""
    return _SHELL_TAIL_RE.split(cmd.strip(), maxsplit=1)[0].rstrip()


def _intercept_workplace_shell(sandbox: Sandbox | None, cmd: str) -> str | None:
    """Route workplace inspection through API host path (same as UI file panel)."""
    sid = _sandbox_id(sandbox)
    core = _strip_shell_tail(cmd)

    # Forbid creating a nested workplace/ directory under the bind mount root
    m = re.match(
        r"^mkdir(?:\s+-p)?\s+(?:/workplace/)?workplace(?:/\S*)?\s*$",
        core,
    )
    if m:
        return (
            "禁止创建 workplace 子目录：路径已相对工作区根目录。"
            "中间产物用 task/<毫秒时间戳>/，最终文件直接写根目录（如 report.xlsx）。"
        )

    m = re.match(r"^ls(?:\s+-[a-zA-Z0-9]+)*\s+(/workplace(?:/[^\s]*)?)/?\s*$", core)
    if m:
        rel = _normalize_workplace_rel(m.group(1))
        if not _is_safe_workplace_rel(rel):
            return None
        return format_dir_listing(sid, rel)

    m = re.match(r"^ls(?:\s+-[a-zA-Z0-9]+)*\s*$", core)
    if m:
        return None  # bare ls — not a workplace listing

    m = re.match(r"^find\s+/workplace(?:/|\s+)(.*)$", core, re.I)
    if m:
        tail = m.group(1).strip()
        if "-name" in tail:
            name_m = re.search(r'-name\s+["\']?([^"\']+)["\']?', tail)
            if name_m:
                matches = find_files(sid, name_m.group(1))
                if not matches:
                    return "(未找到匹配文件)"
                return "\n".join(f"/workplace/{p}" for p in matches)
        return format_dir_listing(sid)

    m = re.match(r"^(?:test|stat)\s+-f\s+(/workplace/[^\s]+)\s*$", core)
    if m:
        rel = _normalize_workplace_rel(m.group(1))
        if not _is_safe_workplace_rel(rel):
            return None
        exists = (workplace_root(_sandbox_id(sandbox)) / rel).is_file()
        return "exists" if exists else "missing"

    return None


def _is_task_py_rel(rel: str) -> bool:
    """True for workplace-relative paths like task/<run>/build_report.py."""
    r = _normalize_workplace_rel(rel or "").replace("\\", "/")
    return bool(_TASK_PY_REL_RE.search(r))


def _block_task_py_write_message(rel_or_cmd: str = "") -> str:
    return (
        "【拦截】禁止往 `task/**/*.py` 写入分析脚本（会触发 API 热重载、中断长任务）。"
        "请写到 `/tmp/build_report.py` 或 `tmp/build_report.py`，再 "
        "`SHELL: python3 /tmp/build_report.py`（xlsx 落到当前目录）。"
        + (f" 已拦截: `{rel_or_cmd[:120]}`" if rel_or_cmd else "")
    )


def _exec_shell(sandbox: Sandbox | None, cmd: str, timeout: int) -> str:
    intercepted = _intercept_workplace_shell(sandbox, cmd)
    if intercepted is not None:
        return intercepted
    if _SHELL_TASK_PY_WRITE_RE.search(cmd or ""):
        return _block_task_py_write_message(cmd)
    bench_root = benchmark_workplace_root()
    if bench_root is not None:
        import subprocess

        proc = subprocess.run(
            cmd,
            shell=True,
            cwd=str(bench_root),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        combined = stdout
        if stderr:
            combined = f"{combined}{stderr}" if combined else stderr
        combined = combined.strip()
        if proc.returncode != 0:
            body = combined or f"shell exited with code {proc.returncode}"
            return f"[exit {proc.returncode}] {body}"
        return combined
    if sandbox and sandbox.container_id:
        return docker_service.exec_in_sandbox(sandbox.container_id, cmd, timeout=timeout)
    return "[no sandbox] " + cmd


def _read_workplace(sandbox: Sandbox | None, rel: str) -> str:
    rel = _normalize_workplace_rel(rel)
    if not _is_safe_workplace_rel(rel):
        return f"非法路径: {rel}"
    wp = workplace_root(_sandbox_id(sandbox)) / rel
    if wp.is_dir():
        return format_dir_listing(_sandbox_id(sandbox), rel)
    if not wp.is_file():
        matches = find_files(_sandbox_id(sandbox), Path(rel).name) if rel else []
        if matches:
            hint = "；".join(matches[:5])
            return f"文件不存在: {rel}（同名校验路径: {hint}，请用 READ: {matches[0]}）"
        return "文件不存在"
    ext = wp.suffix.lower()
    if ext in BINARY_EXTS:
        size_kb = max(1, wp.stat().st_size // 1024)
        return (
            f"[二进制文件] {rel} ({size_kb} KB)。"
            f"请用 READ 读取同目录下的文本说明，或提示用户在界面中下载/预览该文件。"
        )
    return wp.read_text(encoding="utf-8", errors="replace")[:8000]


def _write_workplace(sandbox: Sandbox | None, rel: str, content: str) -> str:
    rel = _normalize_workplace_rel(rel)
    if not rel or not _is_safe_workplace_rel(rel):
        return f"非法写入路径: {rel or '(空)'}"
    if _is_task_py_rel(rel):
        return _block_task_py_write_message(rel)
    ext = Path(rel).suffix.lower()
    if ext in BINARY_EXTS:
        return (
            f"禁止用 WRITE 直接写入二进制文件 `{rel}`。"
            f"请先把数据用 WRITE 落成 task/<毫秒时间戳>/xxx.json 或 xxx.csv，"
            f"再用 SHELL: python3 + pandas/openpyxl 读取该 JSON/CSV 生成 `{rel}`；"
            f"分析脚本写到 /tmp/ 再执行（不要写到 task/，避免触发热重载）。"
        )
    wp = workplace_root(_sandbox_id(sandbox)) / rel
    wp.parent.mkdir(parents=True, exist_ok=True)
    wp.write_text(content, encoding="utf-8")
    return f"已写入 {rel}\n（父目录已自动创建）"


# file_search (react-engine-v8 R3): content grep over the workplace tree.
_SEARCH_MAX_HITS = 200
_SEARCH_MAX_TOTAL_CHARS = 6000
_HIDDEN_DIR_NAMES = {".git", ".venv", "node_modules", "__pycache__"}


def _search_workplace(sandbox: Sandbox | None, query: str) -> str:
    """Recursively grep the workplace for a case-insensitive substring.

    Returns ``file:line: snippet`` lines, capped by hit count and total length.
    Skips binary files and hidden directories.
    """
    query = (query or "").strip()
    if not query:
        return "SEARCH 需要非空 query"
    root = workplace_root(_sandbox_id(sandbox))
    if not root.is_dir():
        return "(无匹配)"
    needle = query.lower()
    hits: list[str] = []
    total = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        parts = path.relative_to(root).parts
        if any(p.startswith(".") or p in _HIDDEN_DIR_NAMES for p in parts[:-1]):
            continue
        if path.suffix.lower() in BINARY_EXTS:
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if b"\x00" in data[:4096]:
            continue  # binary content, not a text file
        try:
            text = data.decode("utf-8", errors="replace")
        except Exception:
            continue
        rel = str(path.relative_to(root)).replace("\\", "/")
        for i, line in enumerate(text.splitlines(), 1):
            if needle in line.lower():
                hits.append(f"{rel}:{i}: {line.strip()[:200]}")
                total += len(hits[-1])
                if len(hits) >= _SEARCH_MAX_HITS or total >= _SEARCH_MAX_TOTAL_CHARS:
                    break
        if len(hits) >= _SEARCH_MAX_HITS or total >= _SEARCH_MAX_TOTAL_CHARS:
            break
    if not hits:
        return "(无匹配)"
    out = "\n".join(hits)
    if len(hits) >= _SEARCH_MAX_HITS or total >= _SEARCH_MAX_TOTAL_CHARS:
        out += f"\n…(结果已截断，至少命中 {len(hits)} 条)"
    return out
