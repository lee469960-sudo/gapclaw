import asyncio
import json
import os
import re
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Skill, MCP, Sandbox, HttpMcp, RagCorpus
from app.services import docker_service, skill_runtime
from app.services.mcp_client import call_mcp_tool
from app.services.httpmcp_runner import call_httpmcp
from app.services.rag_indexer import search_corpus
from app.services.workplace import format_dir_listing, find_files

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


async def execute_action(
    action: str,
    reply: str,
    db: Session,
    agent,
    sandbox: Sandbox | None,
    skill_ids: list[str],
    mcp_ids: list[str],
    httpmcp_ids: list[str] | None = None,
    rag_ids: list[str] | None = None,
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

    if action == "skill_read_script" or reply.startswith("SKILL_READ:"):
        m = re.match(r"SKILL_READ:\s*(\S+)\s*(.*)", reply)
        sid = m.group(1) if m else (skill_ids[0] if skill_ids else "")
        path = m.group(2).strip() if m else ""
        sk = db.query(Skill).filter(Skill.id == sid).first()
        return skill_runtime.read_script(sk, path) if sk else "skill not found"

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
            try:
                args = json.loads(m.group(2).strip())
            except Exception:
                args = {"input": m.group(2).strip()}
        for mid in mcp_ids:
            mcp = db.query(MCP).filter(MCP.id == mid).first()
            if mcp:
                return await call_mcp_tool(mcp, tool, args)
        return "no mcp configured"

    if action == "httpmcp_call" or reply.startswith("HTTPMCP:"):
        m = re.match(r"HTTPMCP:\s*(\S+)\s*(.*)", reply, re.DOTALL)
        hid = m.group(1) if m else ""
        vars_raw = m.group(2).strip() if m and m.group(2) else "{}"
        try:
            variables = json.loads(vars_raw) if vars_raw else {}
        except Exception:
            variables = {"input": vars_raw}
        for hid2 in (httpmcp_ids or []):
            if hid and hid not in hid2:
                hm = db.query(HttpMcp).filter(HttpMcp.id == hid).first()
            else:
                hm = db.query(HttpMcp).filter(HttpMcp.id == hid2).first()
            if hm:
                return await call_httpmcp(hm, variables)
        hm = db.query(HttpMcp).filter(HttpMcp.id == hid).first() if hid else None
        return await call_httpmcp(hm, variables) if hm else "no httpmcp configured"

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

    if action == "self_ask" or reply.startswith("THINK:"):
        return "continue"

    if reply.startswith("FINAL:"):
        return reply[6:].strip()

    return ""


def _sandbox_id(sandbox: Sandbox | None) -> str:
    return sandbox.id if sandbox else "default"


def _benchmark_workplace_root() -> Path | None:
    raw = (os.environ.get("REACT_BENCH_WORKSPACE_ROOT") or "").strip()
    if not raw:
        return None
    return Path(raw).resolve()


def _wp_root(sandbox: Sandbox | None) -> Path:
    bench_root = _benchmark_workplace_root()
    if bench_root is not None:
        return bench_root
    return Path(get_settings().workplace_dir) / _sandbox_id(sandbox) / "workplace"


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
        if " " in part and any(tok in part for tok in ("2>", "1>", "|", "&&", "echo", "head", "ls")):
            return False
    return True


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
        exists = (_wp_root(sandbox) / rel).is_file()
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
    bench_root = _benchmark_workplace_root()
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
    wp = _wp_root(sandbox) / rel
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
            f"请将 JSON/CSV 数据写入 `task/`，由平台落盘最终 xlsx；"
            f"或用 SHELL: + openpyxl/pandas 生成真实二进制文件。"
        )
    wp = _wp_root(sandbox) / rel
    wp.parent.mkdir(parents=True, exist_ok=True)
    wp.write_text(content, encoding="utf-8")
    return f"已写入 {rel}（父目录已自动创建）"
