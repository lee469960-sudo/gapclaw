"""Layered operational kill switches for creation of new Code runs."""

from __future__ import annotations

from app.models import CodeKillSwitch
from app.security import new_id, now_str


SCOPES = ("global", "project", "repository", "tool", "image", "model", "runtime", "storage")


class CodeKillSwitchError(ValueError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def set_code_kill_switch(
    db,
    *,
    scope: str,
    target: str = "*",
    enabled: bool = True,
    reason: str = "",
    updated_by: str = "",
):
    if scope not in SCOPES:
        raise CodeKillSwitchError("code_kill_switch_scope_invalid")
    normalized = "*" if scope == "global" else str(target or "").strip()
    if not normalized:
        raise CodeKillSwitchError("code_kill_switch_target_invalid")
    switch = db.query(CodeKillSwitch).filter(
        CodeKillSwitch.scope == scope,
        CodeKillSwitch.target == normalized,
    ).first()
    if not switch:
        switch = CodeKillSwitch(id=new_id(), scope=scope, target=normalized)
        db.add(switch)
    switch.enabled = bool(enabled)
    switch.reason = str(reason or "")[:128]
    switch.updated_by = str(updated_by or "")[:64]
    switch.updated_at = now_str()
    db.flush()
    return switch


def enforce_code_kill_switches(
    db,
    *,
    project_id: str,
    repository: str,
    tools: list[str],
    image: str,
    model: str,
    runtime: str = "",
) -> None:
    enabled = db.query(CodeKillSwitch).filter(CodeKillSwitch.enabled.is_(True)).all()
    targets = {
        "global": {"*"},
        "project": {project_id},
        "repository": {repository},
        "tool": set(tools),
        "image": {image},
        "model": {model},
        "runtime": {runtime} if runtime else set(),
        "storage": {"capacity"},
    }
    for scope in SCOPES:
        if any(switch.scope == scope and switch.target in targets[scope] for switch in enabled):
            raise CodeKillSwitchError(f"code_kill_switch_{scope}")
