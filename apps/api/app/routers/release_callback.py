"""Fixed Nginx-mTLS forwarded release callback intake."""

from __future__ import annotations

import re
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy.orm import Session

from app.database import get_db
from app.config import get_settings
from app.services.release_config import ReleaseConfigError, ReleaseManagementConfig
from app.services.release_ledger import ReleaseLedger
from app.services.release_runner import ReleaseCallbackService, ReleaseRunnerError


router = APIRouter(tags=["release-internal"])


class ReleaseCallbackPayload(BaseModel):
    """The complete fixed envelope retained by the Runner for retry."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    release_id: str
    git_tag: str
    version: str
    commit_sha: str
    target_id: str
    api_image: str
    web_image: str
    created_at: str
    health_check_version: str
    status: Literal["succeeded", "failed", "rolled_back", "reconciliation_required"]
    occurred_at: str
    health_result: str = ""
    rollback_result: str = ""
    failure_summary: str = ""

    @field_validator("api_image", "web_image")
    @classmethod
    def require_digest_image(cls, value: str) -> str:
        if not re.fullmatch(r"[a-z0-9][a-z0-9./:_-]*@sha256:[a-f0-9]{64}", value):
            raise ValueError("release_digest_invalid")
        return value


@router.post("/internal/release-runner/callback")
def receive_release_callback(
    body: ReleaseCallbackPayload,
    x_gap_runner_client_verify: str | None = Header(default=None, alias="X-Gap-Runner-Client-Verify"),
    db: Session = Depends(get_db),
):
    try:
        config = ReleaseManagementConfig.preview_from_settings(get_settings())
        if body.target_id != config.target_id:
            raise ReleaseRunnerError("release_callback_target_not_allowed")
        return ReleaseCallbackService(ReleaseLedger(db)).accept(
            body.model_dump(), proxy_verified=(x_gap_runner_client_verify or ""),
        )
    except (ReleaseConfigError, ReleaseRunnerError) as exc:
        status_code = 403 if exc.args[0] == "release_runner_client_untrusted" else 422
        raise HTTPException(status_code=status_code, detail=exc.args[0]) from exc
