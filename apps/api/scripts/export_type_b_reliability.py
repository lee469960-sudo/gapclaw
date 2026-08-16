#!/usr/bin/env python3
"""Observe Type-B 14-col export reliability (up to N new sessions).

Hybrid success bar (OpenClaw-aligned):
  roles covered + fallback=false + Chinese header hits ≥10
  (LLM SHELL / 引擎代执行 / 平台辅助 join all count as ok; only 原始回退 fails)

Usage (from apps/api, with venv / PYTHONPATH):
  PYTHONPATH=. python scripts/export_type_b_reliability.py --max 20
  PYTHONPATH=. python scripts/export_type_b_reliability.py --agent-id <id> --max 5

Env:
  EXPORT_AGENT_ID   preferred agent id
  EXPORT_SANDBOX_ID default b5383ea3 (match agent.sandbox_id)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "type_b_us_jul2026_brief.txt"
sys.path.insert(0, str(ROOT))

from app.database import SessionLocal  # noqa: E402
from app.models import Agent  # noqa: E402
from app.services.react_engine import (  # noqa: E402
    _export_analyze_column_texts,
    _parse_export_todos,
    run_react_loop,
)
from app.services.skill_lesson import find_latest_run_state  # noqa: E402
from app.services.workplace import download_path, ensure_workplace, read_deliverable_headers  # noqa: E402


_TARGET_ROLES = ("user", "pay", "cash", "bet", "channel", "game")


def _role_pages(pages: dict) -> dict[str, int]:
    from app.services.react_engine import _view_category

    out = {r: 0 for r in _TARGET_ROLES}
    for view, n in (pages or {}).items():
        role = _view_category(str(view))
        if role in out:
            out[role] += int(n or 0)
    return out


def _header_hits(headers: list[str], want: list[str]) -> int:
    hits = 0
    low = [h.lower() for h in headers]
    for col in want:
        key = col.split("（")[0].split("(")[0].strip().lower()
        if not key:
            continue
        if any(key in h or h in key for h in low):
            hits += 1
    return hits


def evaluate_run(sandbox_id: str, run_id: str, state: dict, want_cols: list[str]) -> dict:
    from app.services.workplace import count_data_rows

    pages = _role_pages(state.get("fetched_view_pages") or {})
    missing = [str(r) for r in (state.get("missing_roles") or []) if r]
    fallback = bool(state.get("fallback"))
    deliverable = str(state.get("deliverable") or "").strip()
    headers: list[str] = []
    deliv_rows = state.get("deliverable_rows")
    if deliverable:
        p = download_path(sandbox_id, deliverable)
        if p and p.is_file():
            headers = read_deliverable_headers(p) or []
            if deliv_rows is None:
                deliv_rows = count_data_rows(p)
    hits = _header_hits(headers, want_cols) if headers else 0
    roles_ok = all(pages.get(r, 0) >= 1 for r in _TARGET_ROLES) and not missing
    # Hybrid: engine-executed / platform-assisted set fallback=false → count as ok
    ok = roles_ok and not fallback and hits >= max(10, len(want_cols) - 2)
    user_pages = state.get("user_pages")
    if user_pages is None:
        user_pages = pages.get("user", 0)
    user_complete = state.get("user_fetch_complete")
    user_trunc = bool(state.get("user_truncated"))
    # Soft signal for cross-run trend (does not fail ok)
    rows_ok = not user_trunc and user_complete is not False
    return {
        "run_id": run_id,
        "ok": ok,
        "fallback": fallback,
        "missing": missing,
        "pages": pages,
        "deliverable": deliverable,
        "header_hits": hits,
        "header_total": len(want_cols),
        "hybrid_ok": ok,
        "deliverable_rows": deliv_rows,
        "user_pages": user_pages,
        "user_fetch_complete": user_complete,
        "user_truncated": user_trunc,
        "rows_ok": rows_ok,
    }


def pick_agent(db, agent_id: str, sandbox_id: str) -> Agent:
    if agent_id:
        agent = db.query(Agent).filter(Agent.id == agent_id).first()
        if not agent:
            raise SystemExit(f"agent not found: {agent_id}")
        return agent
    agents = db.query(Agent).filter(Agent.sandbox_id == sandbox_id).all()
    if not agents:
        # fallback: any agent with ads skill / export-ish name
        agents = db.query(Agent).all()
    if not agents:
        raise SystemExit("no agents in DB; start API and create export agent first")
    # Prefer sandbox match then name hint
    for a in agents:
        if a.sandbox_id == sandbox_id:
            return a
    for a in agents:
        name = (a.name or "").lower()
        if "export" in name or "导出" in (a.name or "") or "ads" in name:
            return a
    return agents[0]


async def one_attempt(agent: Agent, brief: str, username: str) -> dict:
    sid = f"rel_{uuid.uuid4().hex[:12]}"
    db = SessionLocal()
    try:
        # refresh agent on this session
        agent = db.query(Agent).filter(Agent.id == agent.id).first()
        if not agent:
            raise RuntimeError("agent disappeared")
        t0 = time.time()
        await run_react_loop(db, agent, sid, brief, username)
        elapsed = time.time() - t0
        latest = find_latest_run_state(agent.sandbox_id)
        if not latest:
            return {
                "ok": False,
                "session_id": sid,
                "elapsed": elapsed,
                "error": "no _run_state.json",
            }
        run_id, state = latest
        want = _export_analyze_column_texts(_parse_export_todos(brief))
        result = evaluate_run(agent.sandbox_id, run_id, state, want)
        result["session_id"] = sid
        result["elapsed"] = round(elapsed, 1)
        return result
    finally:
        db.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="Type-B export reliability observer")
    ap.add_argument("--max", type=int, default=20, help="max new-session attempts")
    ap.add_argument("--agent-id", default="", help="or EXPORT_AGENT_ID")
    ap.add_argument("--sandbox-id", default="b5383ea3", help="or EXPORT_SANDBOX_ID")
    ap.add_argument("--username", default="admin")
    ap.add_argument("--dry-parse", action="store_true", help="only validate fixture parse")
    args = ap.parse_args()

    import os

    agent_id = args.agent_id or os.environ.get("EXPORT_AGENT_ID", "")
    sandbox_id = args.sandbox_id or os.environ.get("EXPORT_SANDBOX_ID", "b5383ea3")

    if not FIXTURE.is_file():
        print(f"MISSING fixture: {FIXTURE}", file=sys.stderr)
        return 2
    brief = FIXTURE.read_text(encoding="utf-8").strip() + "\n"
    want = _export_analyze_column_texts(_parse_export_todos(brief))
    print(f"fixture columns={len(want)} path={FIXTURE}")
    if len(want) < 14:
        print("WARN: expected 14 columns from fixture", file=sys.stderr)
    if args.dry_parse:
        for i, c in enumerate(want, 1):
            print(f"  {i}.{c}")
        return 0

    db = SessionLocal()
    try:
        agent = pick_agent(db, agent_id, sandbox_id)
        print(f"agent={agent.id} name={agent.name!r} sandbox={agent.sandbox_id}")
        ensure_workplace(agent.sandbox_id)
    finally:
        db.close()

    summaries: list[dict] = []
    for i in range(1, max(1, args.max) + 1):
        print(f"\n=== attempt {i}/{args.max} ===")
        try:
            result = asyncio.run(one_attempt(agent, brief, args.username))
        except Exception as e:
            result = {"ok": False, "error": str(e), "attempt": i}
            print(f"ERROR: {e}")
        summaries.append(result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result.get("ok"):
            print(f"\nSUCCESS on attempt {i}")
            return 0

    print("\n=== summary (no success) ===")
    for s in summaries:
        print(
            f"- run={s.get('run_id')} ok={s.get('ok')} fb={s.get('fallback')} "
            f"miss={s.get('missing')} pages={s.get('pages')} hits={s.get('header_hits')} "
            f"rows={s.get('deliverable_rows')} user_pages={s.get('user_pages')} "
            f"short={s.get('user_fetch_complete')} trunc={s.get('user_truncated')} "
            f"rows_ok={s.get('rows_ok')}"
            + (f" err={s.get('error')}" if s.get("error") else "")
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
