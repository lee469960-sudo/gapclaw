"""Public-but-signed CI Hook; it is intentionally outside all Agent paths."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.services.release_config import ReleaseConfigError, ReleaseManagementConfig
from app.services.release_hook import ReleaseHookConfig, ReleaseHookError, ReleaseHookService
from app.services.release_ledger import ReleaseLedger
from app.services.release_runner import ReleaseRunnerClient


router = APIRouter(tags=["release-internal"])


def _service(db: Session) -> ReleaseHookService:
    settings = get_settings()
    management = ReleaseManagementConfig.from_settings(settings)
    return ReleaseHookService(
        ReleaseLedger(db), ReleaseRunnerClient(management.runner_tls()), ReleaseHookConfig.from_settings(settings),
        target_id=management.target_id,
    )


@router.post("/internal/release-hook", status_code=202)
async def receive_release_hook(
    request: Request,
    x_gap_release_signature: str | None = Header(default=None, alias="X-GAP-Release-Signature"),
    db: Session = Depends(get_db),
):
    try:
        return _service(db).accept(await request.body(), signature=x_gap_release_signature)
    except (ReleaseConfigError, ReleaseHookError) as exc:
        reason = str(exc)
        status_code = 503 if reason == "release_hook_not_configured" else 403
        raise HTTPException(status_code=status_code, detail=reason) from exc
