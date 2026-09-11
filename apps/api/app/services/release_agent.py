"""Deterministic, read-only Release Agent over persisted release records."""

from __future__ import annotations

from dataclasses import dataclass

from app.models import ReleaseLifecycleAudit, ReleaseManifestRecord
from app.services.release_ledger import ReleaseLedger


RELEASE_AGENT_ID = "release-agent"


@dataclass(frozen=True)
class SystemAgentDefinition:
    id: str
    name: str
    capabilities: tuple[str, ...]


RELEASE_AGENT = SystemAgentDefinition(
    id=RELEASE_AGENT_ID,
    name="Release Agent",
    capabilities=("release_status", "release_history", "release_explanation"),
)


def release_audit_dict(audit: ReleaseLifecycleAudit) -> dict[str, str]:
    return {
        "release_id": audit.release_id,
        "target_id": audit.target_id,
        "status": audit.status,
        "health_result": audit.health_result or "",
        "rollback_result": audit.rollback_result or "",
        "failure_summary": audit.failure_summary or "",
        "occurred_at": audit.occurred_at,
    }


class ReleaseAgent:
    """Renders archive-backed release facts; it owns no runtime tool execution."""

    def __init__(self, ledger: ReleaseLedger):
        self.ledger = ledger

    def status(self, *, target_id: str) -> dict[str, object]:
        history = self.ledger.history(target_id=target_id)
        current = self._audit_dict(history[0]) if history else None
        state = "reconciliation_required" if current and current["status"] == "reconciliation_required" else "available"
        return {"agent": self.definition(), "target_id": target_id, "state": state, "current": current}

    def history(self, *, target_id: str, limit: int = 50) -> list[dict[str, str]]:
        return [self._audit_dict(item) for item in self.ledger.history(target_id=target_id)[:limit]]

    def _audit_dict(self, audit: ReleaseLifecycleAudit) -> dict[str, str]:
        result = release_audit_dict(audit)
        manifest = self.ledger.db.get(ReleaseManifestRecord, audit.release_id)
        result["api_image"] = manifest.api_image if manifest else ""
        result["web_image"] = manifest.web_image if manifest else ""
        return result

    def explain(self, *, target_id: str) -> str:
        current = self.status(target_id=target_id)["current"]
        if current is None:
            return "尚无已存档的发布记录。"
        status = current["status"]
        if status == "succeeded":
            return f"发布 {current['release_id']} 已通过健康检查。"
        if status == "rolled_back":
            return f"发布 {current['release_id']} 未通过健康检查，已记录回滚结果。"
        if status == "reconciliation_required":
            return f"发布 {current['release_id']} 需要与 Runner 对账，系统不会自动继续部署。"
        return f"发布 {current['release_id']} 的最近记录状态为 {status}。"

    @staticmethod
    def definition() -> dict[str, object]:
        return {"id": RELEASE_AGENT.id, "name": RELEASE_AGENT.name, "capabilities": list(RELEASE_AGENT.capabilities)}
