"""Sensitive-output redaction and project-scoped artifact authorization."""

from __future__ import annotations

import re
import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

@dataclass(frozen=True)
class ClassifiedOutput:
    text: str
    classification: str
    redaction_count: int


@dataclass(frozen=True)
class SecretScanResult:
    complete: bool
    detected: bool
    content_hash: str
    bytes_scanned: int
    reason: str = ""


_BLOCK_SECRET = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----.*?"
    r"-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    re.DOTALL,
)
_PREFIX_SECRET = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{16,}|"
    r"AKIA[A-Z0-9]{16})\b"
)
_ASSIGNED_SECRET = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|auth[_-]?token|password|passwd|secret)"
    r"(\s*[:=]\s*)([\"']?)[^\s,;\"']{8,}([\"']?)"
)
_BEARER_SECRET = re.compile(r"(?i)\b(authorization\s*:\s*bearer\s+)[A-Za-z0-9._~+/-]{8,}")

_BYTE_SECRET_PATTERNS = (
    re.compile(br"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", re.IGNORECASE),
    re.compile(
        br"\b(?:sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{16,}|"
        br"github_pat_[A-Za-z0-9_]{16,}|AKIA[A-Z0-9]{16})\b"
    ),
    re.compile(
        br"\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|password|passwd|secret)"
        br"\s*[:=]\s*[\"']?[^\s,;\"']{8,}",
        re.IGNORECASE,
    ),
    re.compile(
        br"\bauthorization\s*:\s*bearer\s+[A-Za-z0-9._~+/-]{8,}",
        re.IGNORECASE,
    ),
)
_SCAN_CHUNK_BYTES = 1024 * 1024
_SCAN_OVERLAP_BYTES = 4096


def redact_code_output(value: str) -> ClassifiedOutput:
    text = str(value or "")
    count = 0

    def replace(pattern: re.Pattern, replacement) -> None:
        nonlocal text, count
        text, found = pattern.subn(replacement, text)
        count += found

    replace(_BLOCK_SECRET, "[REDACTED:PRIVATE_KEY]")
    replace(_PREFIX_SECRET, "[REDACTED:SECRET]")
    replace(
        _ASSIGNED_SECRET,
        lambda match: f"{match.group(1)}{match.group(2)}[REDACTED:SECRET]",
    )
    replace(
        _BEARER_SECRET,
        lambda match: f"{match.group(1)}[REDACTED:SECRET]",
    )
    return ClassifiedOutput(
        text=text,
        classification="secret_redacted" if count else "project_source",
        redaction_count=count,
    )


def contains_secret_bytes(value: bytes) -> bool:
    return any(pattern.search(value) for pattern in _BYTE_SECRET_PATTERNS)


def scan_changed_content(
    workspace: str | Path,
    changed_paths: tuple[str, ...] | list[str],
    *,
    chunk_bytes: int = _SCAN_CHUNK_BYTES,
) -> SecretScanResult:
    """Scan and fingerprint every changed byte with bounded memory."""
    root = Path(workspace).resolve()
    facts: list[dict[str, str]] = []
    bytes_scanned = 0
    detected = False
    chunk_size = max(1024, min(int(chunk_bytes), _SCAN_CHUNK_BYTES))
    try:
        for relative in sorted(set(changed_paths)):
            posix = PurePosixPath(str(relative))
            if (
                posix.is_absolute()
                or not posix.parts
                or any(part in {"", ".", ".."} for part in posix.parts)
            ):
                return SecretScanResult(
                    False, False, "", bytes_scanned, "secret_scan_unsupported"
                )
            path = root.joinpath(*posix.parts)
            try:
                before = path.lstat()
            except FileNotFoundError:
                facts.append({"path": posix.as_posix(), "kind": "deleted", "sha256": ""})
                continue
            if stat.S_ISLNK(before.st_mode):
                target = os.readlink(path).encode("utf-8", errors="surrogateescape")
                bytes_scanned += len(target)
                if contains_secret_bytes(target):
                    detected = True
                after = path.lstat()
                if (
                    not stat.S_ISLNK(after.st_mode)
                    or before.st_ino != after.st_ino
                    or before.st_mtime_ns != after.st_mtime_ns
                    or os.readlink(path).encode("utf-8", errors="surrogateescape") != target
                ):
                    return SecretScanResult(
                        False, False, "", bytes_scanned, "secret_scan_truncated"
                    )
                facts.append({
                    "path": posix.as_posix(),
                    "kind": "symlink",
                    "sha256": hashlib.sha256(target).hexdigest(),
                })
                continue
            if not stat.S_ISREG(before.st_mode):
                return SecretScanResult(
                    False, False, "", bytes_scanned, "secret_scan_unsupported"
                )

            digest = hashlib.sha256()
            tail = b""
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags)
            with os.fdopen(descriptor, "rb") as handle:
                opened = os.fstat(handle.fileno())
                if opened.st_ino != before.st_ino or opened.st_dev != before.st_dev:
                    return SecretScanResult(
                        False, False, "", bytes_scanned, "secret_scan_truncated"
                    )
                file_bytes = 0
                while True:
                    chunk = handle.read(chunk_size)
                    if not chunk:
                        break
                    file_bytes += len(chunk)
                    bytes_scanned += len(chunk)
                    digest.update(chunk)
                    window = tail + chunk
                    if contains_secret_bytes(window):
                        detected = True
                    tail = window[-_SCAN_OVERLAP_BYTES:]
                after = os.fstat(handle.fileno())
            current = path.lstat()
            if (
                file_bytes != before.st_size
                or after.st_size != before.st_size
                or after.st_mtime_ns != before.st_mtime_ns
                or current.st_ino != before.st_ino
                or current.st_dev != before.st_dev
                or current.st_mtime_ns != before.st_mtime_ns
            ):
                return SecretScanResult(
                    False, False, "", bytes_scanned, "secret_scan_truncated"
                )
            facts.append({
                "path": posix.as_posix(),
                "kind": "file",
                "sha256": digest.hexdigest(),
            })
    except (OSError, UnicodeError, ValueError):
        return SecretScanResult(False, False, "", bytes_scanned, "secret_scan_failed")
    canonical = json.dumps(facts, sort_keys=True, separators=(",", ":")).encode()
    return SecretScanResult(
        True,
        detected,
        hashlib.sha256(canonical).hexdigest(),
        bytes_scanned,
        "secret_detected" if detected else "",
    )


def can_access_code_artifact(user, artifact, project) -> bool:
    """Artifacts require reviewer role and inherit organization/project scope."""
    from app.services.code_agent.authorization import (
        CodeAuthorizationError,
        require_project_reviewer,
    )

    try:
        require_project_reviewer(user, project, resource=artifact)
    except CodeAuthorizationError:
        return False
    return True
