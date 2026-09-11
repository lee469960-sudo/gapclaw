"""Durable local release state for one fixed deployment target."""

from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import threading
from typing import Iterator

from tools.gap_deploy_runner.release_manifest import ReleaseManifest


_LOCKS: dict[Path, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()
_ACTIVE_PHASES = {"received", "validating", "deploying", "verifying", "rolling_back"}
_CALLBACK_FIELDS = {
    "schema_version", "release_id", "git_tag", "version", "commit_sha", "target_id",
    "api_image", "web_image", "created_at", "health_check_version", "status",
    "occurred_at", "health_result", "rollback_result", "failure_summary",
}
_CALLBACK_REQUIRED = _CALLBACK_FIELDS - {"health_result", "rollback_result", "failure_summary"}
_TERMINAL_CALLBACK_PHASES = {"succeeded", "failed", "rolled_back", "reconciliation_required"}


class ReleaseStateStore:
    def __init__(self, path: Path, *, target_id: str):
        self.path = path
        self.target_id = target_id

    @contextmanager
    def locked(self) -> Iterator[None]:
        with _LOCKS_GUARD:
            lock = _LOCKS.setdefault(self.path.resolve(), threading.Lock())
        with lock:
            yield

    def load(self) -> dict[str, object]:
        if not self.path.exists():
            return self._empty()
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {**self._empty(), "phase": "reconciliation_required"}
        if not isinstance(state, dict) or state.get("target_id") != self.target_id:
            return {**self._empty(), "phase": "reconciliation_required"}
        if not isinstance(state.get("pending_callbacks", []), list):
            return {**self._empty(), "phase": "reconciliation_required"}
        state.setdefault("pending_callbacks", [])
        if state.get("phase") in _ACTIVE_PHASES:
            state["phase"] = "reconciliation_required"
            self._write(state)
        return state

    def record_received(self, manifest: ReleaseManifest) -> dict[str, object]:
        state = self.load()
        state.update({"phase": "received", "current": manifest.to_dict()})
        self._write(state)
        return state

    def record_success(self, manifest: ReleaseManifest) -> dict[str, object]:
        state = self.load()
        history = [item for item in state["success_history"] if item.get("release_id") != manifest.release_id]
        history.append(manifest.to_dict())
        state.update({
            "phase": "succeeded",
            "current": manifest.to_dict(),
            "last_known_healthy": manifest.to_dict(),
            "success_history": history[-5:],
        })
        self._write(state)
        return state

    def record_terminal(self, manifest: ReleaseManifest, *, phase: str) -> dict[str, object]:
        if phase not in _TERMINAL_CALLBACK_PHASES:
            raise ValueError("runner_terminal_phase_invalid")
        state = self.load()
        state.update({"phase": phase, "current": manifest.to_dict()})
        self._write(state)
        return state

    def queue_callback(self, event: dict[str, object]) -> None:
        self._validate_callback(event)
        state = self.load()
        pending = list(state["pending_callbacks"])
        if any(
            item.get("event", {}).get("release_id") == event["release_id"]
            and item.get("event", {}).get("status") == event["status"]
            for item in pending if isinstance(item, dict) and isinstance(item.get("event"), dict)
        ):
            return
        pending.append({"event": dict(event), "attempts": 0, "next_attempt_at": "", "last_error": ""})
        state["pending_callbacks"] = pending
        self._write(state)

    def due_callbacks(self, *, now: str) -> list[dict[str, object]]:
        state = self.load()
        events: list[dict[str, object]] = []
        for item in state["pending_callbacks"]:
            if not isinstance(item, dict) or not isinstance(item.get("event"), dict):
                continue
            next_attempt = str(item.get("next_attempt_at") or "")
            if not next_attempt or next_attempt <= now:
                events.append(dict(item["event"]))
        return events

    def record_callback_failure(self, *, release_id: str, status: str, next_attempt_at: str, reason: str) -> int:
        state = self.load()
        for item in state["pending_callbacks"]:
            if not isinstance(item, dict) or not isinstance(item.get("event"), dict):
                continue
            event = item["event"]
            if event.get("release_id") == release_id and event.get("status") == status:
                attempts = int(item.get("attempts") or 0) + 1
                item.update({"attempts": attempts, "next_attempt_at": next_attempt_at, "last_error": reason[:128]})
                self._write(state)
                return attempts
        return 0

    def callback_attempts(self, *, release_id: str, status: str) -> int:
        state = self.load()
        for item in state["pending_callbacks"]:
            if not isinstance(item, dict) or not isinstance(item.get("event"), dict):
                continue
            event = item["event"]
            if event.get("release_id") == release_id and event.get("status") == status:
                return int(item.get("attempts") or 0)
        return 0

    def record_callback_delivered(self, *, release_id: str, status: str) -> None:
        state = self.load()
        state["pending_callbacks"] = [
            item for item in state["pending_callbacks"]
            if not (
                isinstance(item, dict) and isinstance(item.get("event"), dict)
                and item["event"].get("release_id") == release_id
                and item["event"].get("status") == status
            )
        ]
        self._write(state)

    def _empty(self) -> dict[str, object]:
        return {
            "target_id": self.target_id, "phase": "idle", "current": None,
            "last_known_healthy": None, "success_history": [], "pending_callbacks": [],
        }

    @staticmethod
    def _validate_callback(event: dict[str, object]) -> None:
        if set(event) - _CALLBACK_FIELDS or any(not str(event.get(key) or "") for key in _CALLBACK_REQUIRED):
            raise ValueError("runner_callback_invalid")
        if str(event["status"]) not in _TERMINAL_CALLBACK_PHASES:
            raise ValueError("runner_callback_invalid")

    def _write(self, state: dict[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
        temporary.replace(self.path)
