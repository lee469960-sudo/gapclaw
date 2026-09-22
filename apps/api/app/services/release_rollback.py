"""Administrator-confirmed, input-free rollback orchestration."""

from __future__ import annotations

from typing import Mapping

from app.services.release_ledger import ReleaseLedger, ReleaseLedgerError
from app.services.release_runner import ReleaseRunnerClient, ReleaseRunnerError


ROLLBACK_CONFIRMATION = "ROLLBACK"


class ReleaseRollbackError(ValueError):
    pass


def known_healthy_target(status: Mapping[str, object], *, target_id: str) -> dict[str, str]:
    baseline = status.get("last_known_healthy")
    if not isinstance(baseline, Mapping):
        raise ReleaseRollbackError("release_runner_no_healthy_release")
    release_id = str(baseline.get("release_id") or "")
    baseline_target = str(baseline.get("target_id") or "")
    if not release_id or baseline_target != target_id:
        raise ReleaseRollbackError("release_runner_no_healthy_release")
    return {"release_id": release_id, "target_id": baseline_target}


class ReleaseRollbackService:
    def __init__(self, ledger: ReleaseLedger, runner: ReleaseRunnerClient, *, target_id: str):
        self.ledger, self.runner, self.target_id = ledger, runner, target_id

    def displayed_target(self, *, status_timeout_seconds: float | None = None) -> dict[str, str]:
        if status_timeout_seconds is None:
            status = self.runner.status()
        else:
            status = self.runner.status(timeout_seconds=status_timeout_seconds)
        return known_healthy_target(status, target_id=self.target_id)

    def submit(self, *, displayed_release_id: str, displayed_target_id: str, confirmation: str, requested_by: str) -> dict[str, object]:
        baseline = self.displayed_target()
        if confirmation != ROLLBACK_CONFIRMATION:
            raise ReleaseRollbackError("release_rollback_confirmation_invalid")
        if displayed_release_id != baseline["release_id"] or displayed_target_id != baseline["target_id"]:
            raise ReleaseRollbackError("release_rollback_target_mismatch")
        try:
            request = self.ledger.request_rollback(
                release_id=baseline["release_id"], target_id=baseline["target_id"], requested_by=requested_by,
            )
        except ReleaseLedgerError as exc:
            raise ReleaseRollbackError(exc.reason) from exc
        try:
            result = self.runner.rollback()
        except ReleaseRunnerError as exc:
            self.ledger.finish_rollback_request(request, status="failed")
            raise ReleaseRollbackError("release_runner_rollback_failed") from exc
        self.ledger.finish_rollback_request(request, status="submitted")
        return {"request_id": request.id, "release_id": request.release_id, "status": request.status, "runner": result}
