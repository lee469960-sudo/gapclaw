"""Fail-closed secret scanner with immutable, coverage-bearing reports."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import time
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

from app.services.code_agent.output_security import contains_secret_bytes


SCANNER_NAME = "builtin-secret-pattern"
SCANNER_VERSION = "2"
_CHUNK_BYTES = 1024 * 1024
_OVERLAP_BYTES = 4096
_SOURCE_UNSCANNABLE_WARN_REASONS = {
    "scanner_binary_unsupported",
    "scanner_unsupported_format",
}
_SCAN_FINDING_CLASSIFICATIONS = {
    "secret_pattern",
    "scanner_binary_unsupported",
    "scanner_unsupported_format",
}


@dataclass(frozen=True)
class ScanFinding:
    path: str
    classification: str = "secret_pattern"

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class ScanReport:
    scope: str
    scanner: str
    scanner_version: str
    input_hash: str
    status: str
    complete: bool
    findings: tuple[ScanFinding, ...]
    files_discovered: int
    files_scanned: int
    bytes_discovered: int
    bytes_scanned: int
    skipped_count: int
    truncated_count: int
    failure_reason: str = ""

    @property
    def findings_count(self) -> int:
        return sum(1 for finding in self.findings if finding.classification == "secret_pattern")

    def passes(self, expected_input_hash: str) -> bool:
        return bool(
            self.complete
            and not self.findings
            and self.input_hash
            and self.input_hash == str(expected_input_hash or "")
        )


def scan_report_passes(report: ScanReport, expected_input_hash: str) -> bool:
    return report.passes(expected_input_hash)


def _source_secret_policy_action(raw_policy: str) -> str:
    try:
        policy = json.loads(raw_policy or "{}")
    except (TypeError, json.JSONDecodeError):
        return "block"
    if not isinstance(policy, dict):
        return "block"
    secret_policy = policy.get("secret_policy")
    if not isinstance(secret_policy, dict):
        return "block"
    return "warn" if secret_policy.get("source") == "warn" else "block"


def _source_unscannable_policy_action(raw_policy: str) -> str:
    try:
        policy = json.loads(raw_policy or "{}")
    except (TypeError, json.JSONDecodeError):
        return "block"
    if not isinstance(policy, dict):
        return "block"
    secret_policy = policy.get("secret_policy")
    if not isinstance(secret_policy, dict):
        return "block"
    return "warn" if secret_policy.get("source_unscannable") == "warn" else "block"


def _patch_secret_policy_action(raw_policy: str) -> str:
    try:
        policy = json.loads(raw_policy or "{}")
    except (TypeError, json.JSONDecodeError):
        return "block"
    if not isinstance(policy, dict):
        return "block"
    secret_policy = policy.get("secret_policy")
    if not isinstance(secret_policy, dict):
        return "block"
    return "warn" if secret_policy.get("patch") == "warn" else "block"


def _source_scan_complete_or_warned(report, *, source_unscannable_action: str) -> bool:
    if (
        report.status == "complete"
        and report.complete
        and str(report.failure_reason or "") in {"", "secret_detected"}
    ):
        return True
    return bool(
        source_unscannable_action == "warn"
        and report.status == "incomplete"
        and not report.complete
        and str(report.failure_reason or "") in _SOURCE_UNSCANNABLE_WARN_REASONS
    )


def persist_scan_report(db, report: ScanReport):
    from app.models import CodeScanReport
    from app.security import new_id, now_str

    row = CodeScanReport(
        id=new_id(),
        scope=report.scope,
        input_hash=report.input_hash,
        scanner=report.scanner,
        scanner_version=report.scanner_version,
        status=report.status,
        complete=report.complete,
        findings_count=report.findings_count,
        files_discovered=report.files_discovered,
        files_scanned=report.files_scanned,
        bytes_discovered=report.bytes_discovered,
        bytes_scanned=report.bytes_scanned,
        skipped_count=report.skipped_count,
        truncated_count=report.truncated_count,
        findings=json.dumps(
            [finding.to_dict() for finding in report.findings], sort_keys=True
        ),
        failure_reason=(
            report.failure_reason
            or ("secret_detected" if report.findings_count else "")
        ),
        created_at=now_str(),
    )
    db.add(row)
    db.commit()
    return row


def persisted_scan_report_payload(row) -> dict[str, object]:
    return {
        "id": str(row.id),
        "scope": str(row.scope),
        "input_hash": str(row.input_hash),
        "scanner": str(row.scanner),
        "scanner_version": str(row.scanner_version),
        "status": str(row.status),
        "complete": bool(row.complete),
        "findings_count": int(row.findings_count),
        "files_discovered": int(row.files_discovered),
        "files_scanned": int(row.files_scanned),
        "bytes_discovered": int(row.bytes_discovered),
        "bytes_scanned": int(row.bytes_scanned),
        "skipped_count": int(row.skipped_count),
        "truncated_count": int(row.truncated_count),
        "findings": json.loads(row.findings or "[]"),
        "failure_reason": str(row.failure_reason or ""),
    }


def persisted_scan_report_bytes(row) -> bytes:
    return json.dumps(
        persisted_scan_report_payload(row),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class SourceScanValidationError(RuntimeError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def validate_source_scan_report(db, run, *, adapter=None):
    """Revalidate the sealed source report before any writable run resource."""
    from app.models import CodeScanReport, CodeSourceSnapshot

    source_secret_action = _source_secret_policy_action(
        str(getattr(run, "effective_policy", "") or "")
    )
    source_unscannable_action = _source_unscannable_policy_action(
        str(getattr(run, "effective_policy", "") or "")
    )
    report_id = str(getattr(run, "source_scan_report_id", "") or "")
    snapshot_id = str(getattr(run, "snapshot_id", "") or "")
    report = db.get(CodeScanReport, report_id) if report_id else None
    snapshot = db.get(CodeSourceSnapshot, snapshot_id) if snapshot_id else None
    if report is None or snapshot is None:
        raise SourceScanValidationError("source_scan_failed")
    if (
        report.scope != "source"
        or not _source_scan_complete_or_warned(
            report,
            source_unscannable_action=source_unscannable_action,
        )
        or report.findings_count < 0
        or not report.input_hash
        or snapshot.status != "sealed"
        or snapshot.scan_report_id != report.id
        or snapshot.id != snapshot.content_hash[:16]
        or snapshot.content_hash != str(getattr(run, "snapshot_hash", "") or "")
        or snapshot.resolved_commit != str(getattr(run, "resolved_commit", "") or "")
    ):
        raise SourceScanValidationError("source_scan_failed")
    try:
        findings = json.loads(report.findings or "[]")
    except (TypeError, json.JSONDecodeError) as exc:
        raise SourceScanValidationError("source_scan_failed") from exc
    if (
        not isinstance(findings, list)
        or sum(1 for item in findings if item.get("classification") == "secret_pattern")
        != report.findings_count
        or any(
            not isinstance(item, dict)
            or set(item) - {"path", "classification"}
            or not isinstance(item.get("path"), str)
            or not isinstance(item.get("classification"), str)
            or item.get("classification") not in _SCAN_FINDING_CLASSIFICATIONS
            for item in findings
        )
    ):
        raise SourceScanValidationError("source_scan_failed")
    if report.findings_count:
        if source_secret_action != "warn":
            raise SourceScanValidationError("secret_detected")
    current = (adapter or BuiltinSecretScanner(scope="source")).scan(
        snapshot.storage_path
    )
    if current.findings and source_secret_action != "warn":
        raise SourceScanValidationError("secret_detected")
    if (
        report.scanner != current.scanner
        or report.scanner_version != current.scanner_version
        or not _source_scan_complete_or_warned(
            current,
            source_unscannable_action=source_unscannable_action,
        )
        or not current.input_hash
        or current.input_hash != report.input_hash
    ):
        raise SourceScanValidationError("source_scan_failed")
    return report


class BuiltinSecretScanner:
    def __init__(
        self,
        *,
        scope: str,
        max_file_bytes: int = 8 * 1024 * 1024,
        timeout_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        if scope not in {"source", "patch"}:
            raise ValueError("scanner_scope_invalid")
        if max_file_bytes <= 0 or timeout_seconds <= 0:
            raise ValueError("scanner_limit_invalid")
        self.scope = scope
        self.max_file_bytes = int(max_file_bytes)
        self.timeout_seconds = float(timeout_seconds)
        self.clock = clock

    def scan(
        self,
        root: str | Path,
        paths: tuple[str, ...] | list[str] | None = None,
        *,
        extra_inputs: dict[str, bytes] | None = None,
    ) -> ScanReport:
        started = self.clock()
        facts: list[dict[str, object]] = []
        findings: list[ScanFinding] = []
        files_discovered = 0
        files_scanned = 0
        bytes_discovered = 0
        bytes_scanned = 0
        skipped_count = 0
        truncated_count = 0
        failure_reason = ""

        def timed_out() -> bool:
            return self.clock() - started > self.timeout_seconds

        try:
            base = Path(root).resolve(strict=True)
            if not base.is_dir():
                raise ValueError("scanner_input_invalid")
            if paths is None:
                candidates = [
                    (candidate.relative_to(base), candidate)
                    for candidate in sorted(
                        base.rglob("*"), key=lambda item: item.as_posix()
                    )
                ]
            else:
                candidates = []
                for raw in sorted(set(paths)):
                    relative = PurePosixPath(str(raw).replace("\\", "/"))
                    if (
                        relative.is_absolute()
                        or not relative.parts
                        or any(part in {"", ".", ".."} for part in relative.parts)
                    ):
                        raise ValueError("scanner_path_invalid")
                    candidates.append((relative, base.joinpath(*relative.parts)))
            for relative, candidate in candidates:
                if ".git" in relative.parts:
                    continue
                if timed_out():
                    failure_reason = "scanner_timeout"
                    break
                path = PurePosixPath(*relative.parts).as_posix()
                try:
                    metadata = candidate.lstat()
                except FileNotFoundError:
                    facts.append({"path": path, "kind": "deleted", "sha256": ""})
                    continue
                if stat.S_ISDIR(metadata.st_mode):
                    continue
                files_discovered += 1
                bytes_discovered += int(metadata.st_size)

                if stat.S_ISLNK(metadata.st_mode):
                    target = os.readlink(candidate).encode(
                        "utf-8", errors="surrogateescape"
                    )
                    bytes_scanned += len(target)
                    if contains_secret_bytes(target):
                        findings.append(ScanFinding(path))
                    facts.append({
                        "path": path,
                        "kind": "symlink",
                        "sha256": hashlib.sha256(target).hexdigest(),
                    })
                    files_scanned += 1
                    continue

                if not stat.S_ISREG(metadata.st_mode):
                    skipped_count += 1
                    failure_reason = failure_reason or "scanner_unsupported_format"
                    findings.append(ScanFinding(path, "scanner_unsupported_format"))
                    facts.append({"path": path, "kind": "unsupported"})
                    continue
                if metadata.st_size > self.max_file_bytes:
                    truncated_count += 1
                    failure_reason = failure_reason or "scanner_file_too_large"
                    facts.append({
                        "path": path,
                        "kind": "truncated",
                        "size": int(metadata.st_size),
                    })
                    continue

                digest = hashlib.sha256()
                tail = b""
                binary = False
                flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                descriptor = os.open(candidate, flags)
                with os.fdopen(descriptor, "rb") as handle:
                    opened = os.fstat(handle.fileno())
                    if (
                        opened.st_dev != metadata.st_dev
                        or opened.st_ino != metadata.st_ino
                    ):
                        truncated_count += 1
                        failure_reason = failure_reason or "scanner_input_changed"
                        facts.append({"path": path, "kind": "changed"})
                        continue
                    while True:
                        if timed_out():
                            failure_reason = "scanner_timeout"
                            break
                        chunk = handle.read(_CHUNK_BYTES)
                        if not chunk:
                            break
                        bytes_scanned += len(chunk)
                        digest.update(chunk)
                        if b"\0" in chunk:
                            binary = True
                        window = tail + chunk
                        if contains_secret_bytes(window) and not any(
                            finding.path == path for finding in findings
                        ):
                            findings.append(ScanFinding(path))
                        tail = window[-_OVERLAP_BYTES:]
                    after = os.fstat(handle.fileno())
                if failure_reason == "scanner_timeout":
                    facts.append({"path": path, "kind": "timeout"})
                    break
                current = candidate.lstat()
                if (
                    after.st_size != metadata.st_size
                    or after.st_mtime_ns != metadata.st_mtime_ns
                    or current.st_dev != metadata.st_dev
                    or current.st_ino != metadata.st_ino
                    or current.st_mtime_ns != metadata.st_mtime_ns
                ):
                    truncated_count += 1
                    failure_reason = failure_reason or "scanner_input_changed"
                    facts.append({"path": path, "kind": "changed"})
                    continue
                if binary:
                    skipped_count += 1
                    failure_reason = failure_reason or "scanner_binary_unsupported"
                    findings.append(ScanFinding(path, "scanner_binary_unsupported"))
                    facts.append({
                        "path": path,
                        "kind": "binary",
                        "sha256": digest.hexdigest(),
                    })
                    continue
                files_scanned += 1
                facts.append({
                    "path": path,
                    "kind": "file",
                    "sha256": digest.hexdigest(),
                })
            for path, content in sorted((extra_inputs or {}).items()):
                if timed_out():
                    failure_reason = "scanner_timeout"
                    break
                if not isinstance(path, str) or not path or not isinstance(content, bytes):
                    raise ValueError("scanner_virtual_input_invalid")
                files_discovered += 1
                bytes_discovered += len(content)
                if len(content) > self.max_file_bytes:
                    truncated_count += 1
                    failure_reason = failure_reason or "scanner_file_too_large"
                    facts.append({
                        "path": path,
                        "kind": "virtual_truncated",
                        "size": len(content),
                    })
                    continue
                bytes_scanned += len(content)
                files_scanned += 1
                if contains_secret_bytes(content):
                    findings.append(ScanFinding(path))
                facts.append({
                    "path": path,
                    "kind": "virtual",
                    "sha256": hashlib.sha256(content).hexdigest(),
                })
        except Exception:
            failure_reason = "scanner_crashed"

        complete = not failure_reason
        status = "complete" if complete else (
            "failed" if failure_reason in {"scanner_crashed", "scanner_timeout"}
            else "incomplete"
        )
        canonical = json.dumps(
            facts, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return ScanReport(
            scope=self.scope,
            scanner=SCANNER_NAME,
            scanner_version=SCANNER_VERSION,
            input_hash=hashlib.sha256(canonical).hexdigest(),
            status=status,
            complete=complete,
            findings=tuple(findings),
            files_discovered=files_discovered,
            files_scanned=files_scanned,
            bytes_discovered=bytes_discovered,
            bytes_scanned=bytes_scanned,
            skipped_count=skipped_count,
            truncated_count=truncated_count,
            failure_reason=failure_reason,
        )
