"""Authenticated read-only Release Agent status and history API."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.deps import get_session_user
from app.models import User
from app.schemas import ok
from app.services.release_agent import ReleaseAgent
from app.services.release_config import ReleaseConfigError, ReleaseManagementConfig
from app.services.release_ledger import ReleaseLedger
from app.services.release_rollback import ReleaseRollbackError, ReleaseRollbackService
from app.services.release_runner import ReleaseRunnerClient, ReleaseRunnerError


router = APIRouter(prefix="/api/release-management", tags=["release-management"])
ROLLBACK_TARGET_STATUS_TIMEOUT_SECONDS = 3.0


def _agent(db: Session) -> ReleaseAgent:
    return ReleaseAgent(ReleaseLedger(db))


def _require_release_admin(user: User) -> None:
    try:
        roles = json.loads(user.roles or "[]")
    except json.JSONDecodeError:
        roles = []
    if "master" not in roles and "admin" not in roles:
        raise HTTPException(status_code=403, detail="release_rollback_admin_required")


def _rollback_service(db: Session) -> ReleaseRollbackService:
    config = ReleaseManagementConfig.from_settings(get_settings())
    return ReleaseRollbackService(
        ReleaseLedger(db), ReleaseRunnerClient(config.runner_tls()), target_id=config.target_id,
    )


def _release_config() -> ReleaseManagementConfig:
    """Read the single process-managed deployment bundle; requests cannot select it."""
    return ReleaseManagementConfig.preview_from_settings(get_settings())


class ReleaseRollbackBody(BaseModel):
    displayed_release_id: str
    displayed_target_id: str
    confirmation: str


@router.get("/config")
def release_config(_user: User = Depends(get_session_user)):
    return ok(_release_config().public_view())


@router.get("/status")
def release_status(
    _user: User = Depends(get_session_user),
    db: Session = Depends(get_db),
):
    return ok(_agent(db).status(target_id=_release_config().target_id))


@router.get("/history")
def release_history(
    limit: int = Query(50, ge=1, le=50),
    _user: User = Depends(get_session_user),
    db: Session = Depends(get_db),
):
    agent = _agent(db)
    target_id = _release_config().target_id
    return ok({"agent": agent.definition(), "target_id": target_id, "items": agent.history(target_id=target_id, limit=limit)})


@router.get("/rollback-target")
def rollback_target(user: User = Depends(get_session_user), db: Session = Depends(get_db)):
    _require_release_admin(user)
    try:
        return ok(_rollback_service(db).displayed_target(
            status_timeout_seconds=ROLLBACK_TARGET_STATUS_TIMEOUT_SECONDS,
        ))
    except ReleaseConfigError as exc:
        raise HTTPException(status_code=503, detail=exc.args[0]) from exc
    except (ReleaseRollbackError, ReleaseRunnerError) as exc:
        raise HTTPException(status_code=409, detail=exc.args[0]) from exc


@router.post("/rollback")
def rollback_release(body: ReleaseRollbackBody, user: User = Depends(get_session_user), db: Session = Depends(get_db)):
    _require_release_admin(user)
    try:
        return ok(_rollback_service(db).submit(
            displayed_release_id=body.displayed_release_id,
            displayed_target_id=body.displayed_target_id,
            confirmation=body.confirmation,
            requested_by=user.username,
        ))
    except ReleaseConfigError as exc:
        raise HTTPException(status_code=503, detail=exc.args[0]) from exc
    except ReleaseRollbackError as exc:
        status_code = 409 if exc.args[0] == "release_runner_no_healthy_release" else 400
        raise HTTPException(status_code=status_code, detail=exc.args[0]) from exc
