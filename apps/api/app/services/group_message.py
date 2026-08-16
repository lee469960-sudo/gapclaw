"""Parse @mentions in group chat messages (shared vs distribution mode)."""

import re

from sqlalchemy.orm import Session

from app.models import Agent


def _member_lookup(members: list[dict], db: Session) -> dict[str, dict]:
    lookup: dict[str, dict] = {}
    for m in members:
        aid = m.get("agent_id") or ""
        if not aid:
            continue
        lookup[aid.lower()] = m
        alias = (m.get("name") or "").strip()
        if alias:
            lookup[alias.lower()] = m
        agent = db.query(Agent).filter(Agent.id == aid).first()
        if agent and agent.name:
            lookup[agent.name.lower()] = m
    return lookup


def parse_group_targets(message: str, members: list[dict], db: Session) -> tuple[str, list[dict]]:
    """
    Returns (mode, targets) where mode is 'distribution' | 'shared' | 'all'
    and targets are member dicts with optional per-member task in '_task'.
    """
    text = (message or "").strip()
    if not members:
        return "all", []

    lookup = _member_lookup(members, db)

    dist_matches = re.findall(r"@(\S+):\s*([^\n@]+)", text)
    if dist_matches:
        targets = []
        seen: set[str] = set()
        for tag, task in dist_matches:
            m = lookup.get(tag.lower().rstrip(",:;"))
            if not m:
                continue
            aid = m.get("agent_id")
            if aid in seen:
                continue
            seen.add(aid)
            item = {**m, "_task": task.strip()}
            targets.append(item)
        if targets:
            return "distribution", targets

    mention_tags = re.findall(r"@(\S+)", text)
    if mention_tags:
        targets = []
        seen: set[str] = set()
        for tag in mention_tags:
            m = lookup.get(tag.lower().rstrip(",:;"))
            if not m:
                continue
            aid = m.get("agent_id")
            if aid in seen:
                continue
            seen.add(aid)
            item = {**m, "_task": re.sub(r"@\S+\s*", "", text).strip() or text}
            targets.append(item)
        if targets:
            return "shared", targets

    return "all", [{**m, "_task": text} for m in members]
