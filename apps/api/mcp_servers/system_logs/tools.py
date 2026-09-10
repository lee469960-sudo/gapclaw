"""Read-only tools for GAP local runtime logs under LOG_DIR (.local/logs)."""

from __future__ import annotations

import os
import re
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

MAX_CHARS = 80_000
MAX_LINES = 2000
DEFAULT_LINES = 200

SOURCE_FILES = {
    "api": "api.log",
    "web": "web.log",
    "cloudflared": "cloudflared.log",
}

_LEVEL_RE = re.compile(
    r"\b(ERROR|WARNING|WARN|CRITICAL|FATAL|Traceback|Exception)\b",
    re.I,
)
_STATUS_RE = re.compile(r'"\s([1-5]\d{2})\s')
_EXC_TYPE_RE = re.compile(r"^([A-Za-z_][\w.]*(?:Error|Exception|Timeout))\b")
_STRUCTURED_CLASS_RE = re.compile(
    r"\bcomponent=(?P<component>[A-Za-z0-9_.-]+)\s+class=(?P<class>[A-Za-z0-9_.-]+)"
)
_TS_RE = re.compile(
    r"(?P<iso>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})"
    r"|(?P<clock>\d{1,2}:\d{2}:\d{2}\s*(?:AM|PM)?)",
    re.I,
)


def log_dir() -> Path:
    raw = (os.environ.get("LOG_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    # apps/api/mcp_servers/system_logs/tools.py → repo root
    here = Path(__file__).resolve()
    repo = here.parents[4]  # system_logs→mcp_servers→api→apps→repo
    return (repo / ".local" / "logs").resolve()


def _clip(text: str, limit: int | None = None) -> str:
    text = text or ""
    cap = MAX_CHARS if limit is None else limit
    if len(text) <= cap:
        return text
    return text[: cap - 40] + "\n…(已截断，请缩小 lines/grep 范围)"


def _safe_source_path(source: str) -> Path | None:
    key = (source or "").strip().lower()
    if key not in SOURCE_FILES:
        return None
    root = log_dir()
    path = (root / SOURCE_FILES[key]).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    if ".." in path.parts:
        return None
    return path


def list_log_sources() -> dict[str, Any]:
    root = log_dir()
    sources: list[dict[str, Any]] = []
    for key, name in SOURCE_FILES.items():
        path = root / name
        item: dict[str, Any] = {
            "source": key,
            "filename": name,
            "exists": path.is_file(),
            "size_bytes": path.stat().st_size if path.is_file() else 0,
            "mtime": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
            if path.is_file()
            else None,
        }
        sources.append(item)
    sources.append(_im_events_source_meta())
    return {"log_dir": str(root), "sources": sources}


def _im_events_source_meta() -> dict[str, Any]:
    """Real size/mtime for im_event_logs (sqlite file or COUNT proxy)."""
    path = _sqlite_path()
    if path is not None:
        file_size = path.stat().st_size if path.is_file() else 0
        mtime: str | None = None
        count = 0
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            try:
                row = conn.execute(
                    "SELECT MAX(created_at), COUNT(*) FROM im_event_logs"
                ).fetchone()
            finally:
                conn.close()
            if row and row[0]:
                mtime = str(row[0])
            count = int(row[1] or 0) if row else 0
        except Exception as e:
            return {
                "source": "im_events",
                "filename": "im_event_logs (DB)",
                "exists": True,
                "size_bytes": file_size,
                "mtime": None,
                "note": f"sqlite 可读但查询失败: {e}",
            }
        # file_size + row_count: sqlite page cache may not grow file on first inserts
        return {
            "source": "im_events",
            "filename": str(path.name),
            "exists": True,
            "size_bytes": file_size + count,
            "mtime": mtime,
            "note": f"DATABASE_URL sqlite → {path}; size=file+rows({count})",
        }

    # Non-sqlite: COUNT(*) as proxy size so growth is observable
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        return {
            "source": "im_events",
            "filename": "im_event_logs (DB)",
            "exists": False,
            "size_bytes": 0,
            "mtime": None,
            "note": "DATABASE_URL 未配置",
        }
    try:
        from sqlalchemy import create_engine, text

        eng = create_engine(url)
        with eng.connect() as conn:
            row = conn.execute(
                text("SELECT COUNT(*), MAX(created_at) FROM im_event_logs")
            ).fetchone()
        count = int(row[0] or 0) if row else 0
        mtime = str(row[1]) if row and row[1] else None
        return {
            "source": "im_events",
            "filename": "im_event_logs (DB)",
            "exists": True,
            "size_bytes": count,
            "mtime": mtime,
            "note": "非 sqlite：size_bytes=COUNT(*) 代理；需 DATABASE_URL 指向平台库",
        }
    except Exception as e:
        return {
            "source": "im_events",
            "filename": "im_event_logs (DB)",
            "exists": False,
            "size_bytes": 0,
            "mtime": None,
            "note": f"无法读取 im_event_logs: {e}",
        }


def _read_tail_lines(path: Path, n: int) -> list[str]:
    n = max(1, min(int(n), MAX_LINES))
    if not path.is_file():
        return []
    # Efficient-ish tail for moderate files
    data = path.read_bytes()
    if not data:
        return []
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    return lines[-n:]


def tail_log(source: str, lines: int = DEFAULT_LINES, grep: str = "") -> str:
    key = (source or "").strip().lower()
    if key == "im_events":
        return _im_events_tail(lines=lines, grep=grep)

    path = _safe_source_path(key)
    if path is None:
        return (
            f"错误: 未知 source={source!r}；"
            f"可用: {', '.join([*SOURCE_FILES, 'im_events'])}"
        )
    if not path.is_file():
        return f"错误: 日志不存在 {path.name}（目录 {log_dir()}）"

    n = max(1, min(int(lines or DEFAULT_LINES), MAX_LINES))
    selected = _read_tail_lines(path, n)
    pattern = (grep or "").strip()
    if pattern:
        try:
            cre = re.compile(pattern, re.I)
            selected = [ln for ln in selected if cre.search(ln)]
        except re.error:
            selected = [ln for ln in selected if pattern.lower() in ln.lower()]

    header = f"# source={key} file={path.name} lines={len(selected)} grepped={bool(pattern)}"
    return _clip(header + "\n" + "\n".join(selected))


def search_log(
    source: str = "api",
    query: str = "",
    level: str = "",
    minutes: int = 0,
    limit: int = 100,
) -> str:
    key = (source or "api").strip().lower()
    if key == "im_events":
        return _im_events_search(query=query, level=level, minutes=minutes, limit=limit)

    path = _safe_source_path(key)
    if path is None:
        return f"错误: 未知 source={source!r}"
    if not path.is_file():
        return f"错误: 日志不存在 {path.name}"

    limit = max(1, min(int(limit or 100), MAX_LINES))
    minutes = max(0, int(minutes or 0))
    q = (query or "").strip()
    lvl = (level or "").strip().upper()
    if lvl in ("WARN",):
        lvl = "WARNING"

    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    cutoff = None
    if minutes > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)

    hits: list[str] = []
    for ln in lines:
        if lvl:
            low = ln.lower()
            if lvl == "TRACEBACK":
                if "traceback" not in low and "exception" not in low:
                    continue
            elif not re.search(rf"\b{re.escape(lvl)}\b", ln, re.I):
                if lvl == "ERROR" and ("traceback" in low or "exception" in low):
                    pass
                else:
                    continue
        if q and q.lower() not in ln.lower():
            continue
        if cutoff is not None:
            ts = _parse_line_time(ln)
            if ts is not None and ts < cutoff.replace(tzinfo=None):
                continue
        hits.append(ln)

    # Prefer recent
    hits = hits[-limit:]
    header = (
        f"# search source={key} query={q!r} level={lvl or '-'} "
        f"minutes={minutes or '-'} hits={len(hits)}"
    )
    return _clip(header + "\n" + "\n".join(hits))


def log_stats(source: str = "api", minutes: int = 60) -> dict[str, Any]:
    key = (source or "api").strip().lower()
    minutes = max(0, int(minutes))
    if key == "im_events":
        return _im_events_stats(minutes=minutes)

    path = _safe_source_path(key)
    if path is None:
        return {"error": f"未知 source={source!r}"}
    if not path.is_file():
        return {"error": f"日志不存在 {path.name}", "log_dir": str(log_dir())}

    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    cutoff = None
    if minutes > 0:
        cutoff = datetime.now() - timedelta(minutes=minutes)

    level_counts: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    exc_counts: Counter[str] = Counter()
    structured_counts: Counter[str] = Counter()
    traceback_blocks = 0
    considered = 0

    for ln in lines:
        if cutoff is not None:
            ts = _parse_line_time(ln)
            if ts is not None and ts < cutoff:
                continue
        considered += 1
        m = _LEVEL_RE.search(ln)
        if m:
            label = m.group(1).upper()
            if label == "WARN":
                label = "WARNING"
            level_counts[label] += 1
            if label == "TRACEBACK":
                traceback_blocks += 1
        sm = _STATUS_RE.search(ln)
        if sm:
            status_counts[sm.group(1)] += 1
        em = _EXC_TYPE_RE.match(ln.strip())
        if em:
            exc_counts[em.group(1)] += 1
        cm = _STRUCTURED_CLASS_RE.search(ln)
        if cm:
            structured_counts[f"{cm.group('component')}:{cm.group('class')}"] += 1

    return {
        "source": key,
        "file": path.name,
        "window_minutes": minutes or "all",
        "lines_considered": considered,
        "file_lines_total": len(lines),
        "level_counts": dict(level_counts.most_common(20)),
        "http_status_counts": dict(status_counts.most_common(20)),
        "top_exceptions": dict(exc_counts.most_common(15)),
        "structured_class_counts": dict(structured_counts.most_common(20)),
        "traceback_mentions": level_counts.get("TRACEBACK", 0) + traceback_blocks,
        "size_bytes": path.stat().st_size,
        "log_dir": str(log_dir()),
    }


def _parse_line_time(line: str) -> datetime | None:
    m = _TS_RE.search(line or "")
    if not m:
        return None
    iso = m.group("iso")
    if iso:
        try:
            return datetime.fromisoformat(iso.replace("Z", "").replace("T", " ")[:19])
        except ValueError:
            return None
    clock = m.group("clock")
    if clock:
        # Vite style without date — cannot filter by absolute window reliably
        return None
    return None


def _sqlite_path() -> Path | None:
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url.startswith("sqlite"):
        return None
    # sqlite:///./data/gap.db or sqlite:////abs/path
    raw = url.split("sqlite:", 1)[-1]
    raw = raw.lstrip("/")
    if url.startswith("sqlite:////"):
        path = Path("/" + url[len("sqlite:////") :])
    elif url.startswith("sqlite:///"):
        rest = url[len("sqlite:///") :]
        path = Path(rest)
        if not path.is_absolute():
            api_dir = Path(__file__).resolve().parents[2]  # apps/api
            path = (api_dir / rest).resolve()
    else:
        path = Path(unquote(urlparse(url).path or ""))
    if path.is_file():
        return path.resolve()
    return None


def _im_events_tail(lines: int = DEFAULT_LINES, grep: str = "") -> str:
    path = _sqlite_path()
    if not path:
        return "错误: im_events 需要可访问的 SQLITE DATABASE_URL（当前未配置或非 sqlite）"
    n = max(1, min(int(lines or DEFAULT_LINES), MAX_LINES))
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        cur = conn.execute(
            "SELECT created_at, level, channel_id, message FROM im_event_logs "
            "ORDER BY id DESC LIMIT ?",
            (n,),
        )
        rows = cur.fetchall()
        conn.close()
    except Exception as e:
        return f"错误: 读取 im_event_logs 失败: {e}"

    out_lines = [f"{a}\t{b}\t{c}\t{d}" for a, b, c, d in reversed(rows)]
    pattern = (grep or "").strip()
    if pattern:
        out_lines = [ln for ln in out_lines if pattern.lower() in ln.lower()]
    return _clip(f"# source=im_events rows={len(out_lines)}\n" + "\n".join(out_lines))


def _im_events_search(
    query: str = "",
    level: str = "",
    minutes: int = 0,
    limit: int = 100,
) -> str:
    path = _sqlite_path()
    if not path:
        return "错误: im_events 需要 SQLITE DATABASE_URL"
    limit = max(1, min(int(limit or 100), MAX_LINES))
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        sql = "SELECT created_at, level, channel_id, message FROM im_event_logs WHERE 1=1"
        params: list[Any] = []
        if level:
            sql += " AND lower(level)=lower(?)"
            params.append(level.strip())
        if query:
            sql += " AND (message LIKE ? OR detail LIKE ?)"
            like = f"%{query}%"
            params.extend([like, like])
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit * 3 if minutes else limit)
        rows = conn.execute(sql, params).fetchall()
        conn.close()
    except Exception as e:
        return f"错误: 搜索 im_event_logs 失败: {e}"

    if minutes > 0:
        cutoff = datetime.now() - timedelta(minutes=minutes)
        filtered = []
        for a, b, c, d in rows:
            try:
                ts = datetime.fromisoformat(str(a)[:19])
            except Exception:
                ts = None
            if ts is None or ts >= cutoff:
                filtered.append((a, b, c, d))
        rows = filtered[:limit]
    else:
        rows = rows[:limit]

    out = [f"{a}\t{b}\t{c}\t{d}" for a, b, c, d in reversed(rows)]
    return _clip(f"# search im_events hits={len(out)}\n" + "\n".join(out))


def _im_events_stats(minutes: int = 60) -> dict[str, Any]:
    path = _sqlite_path()
    if not path:
        return {"error": "im_events 需要 SQLITE DATABASE_URL", "source": "im_events"}
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        rows = conn.execute(
            "SELECT level, message, created_at FROM im_event_logs ORDER BY id DESC LIMIT 2000"
        ).fetchall()
        conn.close()
    except Exception as e:
        return {"error": str(e), "source": "im_events"}

    cutoff = datetime.now() - timedelta(minutes=max(0, minutes)) if minutes else None
    levels: Counter[str] = Counter()
    considered = 0
    for level, _msg, created in rows:
        if cutoff is not None:
            try:
                ts = datetime.fromisoformat(str(created)[:19])
            except Exception:
                ts = None
            if ts is not None and ts < cutoff:
                continue
        considered += 1
        levels[str(level or "info").lower()] += 1
    return {
        "source": "im_events",
        "window_minutes": minutes or "all",
        "rows_considered": considered,
        "level_counts": dict(levels.most_common()),
    }


def dispatch(tool: str, arguments: dict | None) -> str:
    args = arguments if isinstance(arguments, dict) else {}
    name = (tool or "").strip()
    if name == "list_log_sources":
        import json

        return json.dumps(list_log_sources(), ensure_ascii=False, indent=2)
    if name == "tail_log":
        return tail_log(
            source=str(args.get("source") or "api"),
            lines=int(args.get("lines") or DEFAULT_LINES),
            grep=str(args.get("grep") or ""),
        )
    if name == "search_log":
        return search_log(
            source=str(args.get("source") or "api"),
            query=str(args.get("query") or args.get("grep") or ""),
            level=str(args.get("level") or ""),
            minutes=int(args.get("minutes") or 0),
            limit=int(args.get("limit") or 100),
        )
    if name == "log_stats":
        import json

        minutes = 60
        if "minutes" in args and args.get("minutes") is not None and args.get("minutes") != "":
            minutes = int(args.get("minutes"))
        return json.dumps(
            log_stats(
                source=str(args.get("source") or "api"),
                minutes=minutes,
            ),
            ensure_ascii=False,
            indent=2,
        )
    return f"MCP 错误: Unknown tool: {name}"
