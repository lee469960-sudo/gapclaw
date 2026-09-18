"""Side effects owned by the verified release lifecycle, never by an LLM."""

from __future__ import annotations

import json
import logging
from typing import Mapping

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    ImChannel,
    ReleaseLifecycleAudit,
    ReleaseManifestRecord,
    ReleaseNotificationDelivery,
    ReleaseVersionSync,
    SiteConfig,
)
from app.security import new_id, now_str
from app.services.channels.base import create_adapter
from app.services.release_agent import RELEASE_AGENT_ID

logger = logging.getLogger(__name__)


class ReleaseLifecycleError(ValueError):
    pass


def _manifest_for(db: Session, release_id: str) -> ReleaseManifestRecord:
    record = db.get(ReleaseManifestRecord, release_id)
    if record is None:
        raise ReleaseLifecycleError("release_manifest_missing")
    return record


def sync_site_version(
    db: Session,
    *,
    release_id: str,
    target_id: str,
    transition: str = "succeeded",
    source: str = "release-agent",
) -> dict[str, object]:
    """Apply only a verified healthy manifest to the persisted site version."""
    if source != RELEASE_AGENT_ID and source != "release-agent":
        raise ReleaseLifecycleError("release_version_sync_source_forbidden")
    if transition != "succeeded":
        raise ReleaseLifecycleError("release_version_sync_transition_invalid")
    manifest = _manifest_for(db, release_id)
    if manifest.target_id != target_id or not manifest.version or manifest.version != manifest.git_tag:
        raise ReleaseLifecycleError("release_version_sync_manifest_invalid")
    audit = db.query(ReleaseLifecycleAudit).filter_by(
        release_id=release_id, target_id=target_id, status="succeeded",
    ).first()
    if audit is None or (audit.health_result or "").lower() not in {"ok", "healthy", "passed", "success"}:
        raise ReleaseLifecycleError("release_version_sync_requires_healthy_release")

    existing = db.query(ReleaseVersionSync).filter_by(
        release_id=release_id, transition=transition,
    ).first()
    if existing:
        return {
            "release_id": release_id, "version": existing.version,
            "state": "idempotent", "sync_id": existing.id,
        }

    cfg = db.query(SiteConfig).filter(SiteConfig.key == "version").first()
    if not cfg:
        cfg = SiteConfig(key="version", value=manifest.version)
        db.add(cfg)
    else:
        cfg.value = manifest.version
    row = ReleaseVersionSync(
        id=new_id(), release_id=release_id, target_id=target_id,
        transition=transition, version=manifest.version, commit_sha=manifest.commit_sha,
        state="applied", detail="verified healthy release", occurred_at=now_str(),
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.query(ReleaseVersionSync).filter_by(
            release_id=release_id, transition=transition,
        ).first()
        if existing:
            return {"release_id": release_id, "version": existing.version, "state": "idempotent", "sync_id": existing.id}
        raise
    return {"release_id": release_id, "version": manifest.version, "state": "applied", "sync_id": row.id}


def sanitized_release_summary(db: Session, audit: ReleaseLifecycleAudit) -> dict[str, object]:
    manifest = db.get(ReleaseManifestRecord, audit.release_id)
    return {
        "release_id": audit.release_id,
        "status": audit.status,
        "version": manifest.version if manifest else "",
        "git_tag": manifest.git_tag if manifest else "",
        "commit_sha": manifest.commit_sha if manifest else "",
        "target_id": audit.target_id,
        "api_image": manifest.api_image if manifest else "",
        "web_image": manifest.web_image if manifest else "",
        "health_result": audit.health_result or "",
        "rollback_result": audit.rollback_result or "",
        "failure_summary": audit.failure_summary or "",
        "occurred_at": audit.occurred_at or "",
    }


def render_release_notification(summary: Mapping[str, object]) -> str:
    status = str(summary.get("status") or "unknown")
    labels = {"succeeded": "成功", "failed": "失败", "rolled_back": "已回滚", "reconciliation_required": "需对账"}
    lines = [
        f"GAP 发布{labels.get(status, status)}",
        f"版本: {summary.get('version') or summary.get('git_tag') or '-'}",
        f"提交: {summary.get('commit_sha') or '-'}",
        f"目标: {summary.get('target_id') or '-'}",
        f"健康检查: {summary.get('health_result') or '-'}",
        f"API: {summary.get('api_image') or '-'}",
        f"Web: {summary.get('web_image') or '-'}",
    ]
    if summary.get("rollback_result"):
        lines.append(f"回滚: {summary['rollback_result']}")
    if summary.get("failure_summary"):
        lines.append(f"摘要: {summary['failure_summary']}")
    return "\n".join(lines)


async def notify_release_transition(db: Session, *, release_id: str, status: str) -> dict[str, int]:
    audit = db.query(ReleaseLifecycleAudit).filter_by(release_id=release_id, status=status).first()
    if audit is None:
        return {"sent": 0, "failed": 0, "skipped": 0}
    summary = sanitized_release_summary(db, audit)
    text = render_release_notification(summary)
    channels = db.query(ImChannel).filter_by(
        provider="feishu", agent_id=RELEASE_AGENT_ID, enabled=True,
    ).all()
    if not channels:
        existing = db.query(ReleaseNotificationDelivery).filter_by(
            release_id=release_id, transition=status, channel_id="",
        ).first()
        if existing:
            return {"sent": 0, "failed": 0, "skipped": 1}
        row = ReleaseNotificationDelivery(
            id=new_id(), release_id=release_id, transition=status, channel_id="",
            state="missing_channel", payload=json.dumps(summary, ensure_ascii=False), attempted_at=now_str(),
        )
        db.add(row)
        db.commit()
        return {"sent": 0, "failed": 0, "skipped": 1}

    counts = {"sent": 0, "failed": 0, "skipped": 0}
    for channel in channels:
        existing = db.query(ReleaseNotificationDelivery).filter_by(
            release_id=release_id, transition=status, channel_id=channel.id,
        ).first()
        if existing and existing.state == "sent":
            counts["skipped"] += 1
            continue
        if existing is None:
            existing = ReleaseNotificationDelivery(
                id=new_id(), release_id=release_id, transition=status, channel_id=channel.id,
                payload=json.dumps(summary, ensure_ascii=False), attempted_at=now_str(),
            )
            db.add(existing)
        destination = str((channel.get_config() or {}).get("release_chat_id") or (channel.get_config() or {}).get("chat_id") or "")
        if not destination:
            existing.state, existing.error = "missing_destination", "release_chat_id_not_configured"
            counts["failed"] += 1
            db.commit()
            continue
        try:
            adapter = create_adapter(channel.provider, channel.id, channel.get_config())
            await adapter.send_text(destination, text)
            existing.state, existing.error, existing.delivered_at = "sent", "", now_str()
            counts["sent"] += 1
        except Exception as exc:
            existing.state, existing.error = "failed", str(exc)[:2000]
            counts["failed"] += 1
        db.commit()
    return counts


def notify_release_transition_bg(release_id: str, status: str) -> None:
    from app.database import SessionLocal
    import asyncio
    db = SessionLocal()
    try:
        asyncio.run(notify_release_transition(db, release_id=release_id, status=status))
    finally:
        db.close()
