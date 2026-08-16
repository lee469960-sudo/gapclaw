"""Browser console ingest → web.log (for system-logs MCP)."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.deps import get_session_user
from app.models import User
from app.schemas import fail, ok

logger = logging.getLogger(__name__)
router = APIRouter(tags=["console"])

_MAX_BODY_CHARS = 8000
_MAX_ENTRIES = 20
_web_logger: logging.Logger | None = None


def _resolve_web_log_path() -> Path:
    env = (os.environ.get("GAP_WEB_LOG") or "").strip()
    if env:
        return Path(env)
    # apps/api/app/routers → repo root .local/logs/web.log
    root = Path(__file__).resolve().parents[4]
    return root / ".local" / "logs" / "web.log"


def _ensure_web_logger() -> logging.Logger:
    global _web_logger
    if _web_logger is not None:
        return _web_logger
    path = _resolve_web_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lg = logging.getLogger("gap.web.console")
    lg.setLevel(logging.INFO)
    lg.propagate = False
    if not any(
        isinstance(h, logging.FileHandler) and getattr(h, "baseFilename", "") == str(path)
        for h in lg.handlers
    ):
        fh = logging.FileHandler(path, encoding="utf-8")
        fh.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s [web] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        lg.addHandler(fh)
    _web_logger = lg
    return lg


class ConsoleEntry(BaseModel):
    level: str = "error"
    message: str = ""
    stack: str = ""
    url: str = ""
    ts: str = ""


class ConsoleBody(BaseModel):
    entries: list[ConsoleEntry] = Field(default_factory=list)


@router.post("/api/console")
async def ingest_console(
    body: ConsoleBody,
    request: Request,
    user: User = Depends(get_session_user),
):
    entries = list(body.entries or [])[:_MAX_ENTRIES]
    if not entries:
        return fail("empty entries")
    lg = _ensure_web_logger()
    written = 0
    for ent in entries:
        level = (ent.level or "error").strip().lower()
        msg = (ent.message or "")[:2000]
        stack = (ent.stack or "")[:3000]
        url = (ent.url or str(request.headers.get("referer") or ""))[:500]
        ts = ent.ts or datetime.now(timezone.utc).isoformat(timespec="seconds")
        line = f"user={user.username} url={url} ts={ts} {msg}"
        if stack:
            line = f"{line} | {stack}"
        line = line[:_MAX_BODY_CHARS]
        if level in ("warn", "warning"):
            lg.warning("%s", line)
        elif level in ("info", "debug"):
            lg.info("%s", line)
        else:
            lg.error("%s", line)
        written += 1
    return ok({"written": written})
