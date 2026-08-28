"""Canonical remote Git source parsing and exact pre-connect allowlist policy."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib.parse import quote, unquote, urlsplit


_DEFAULT_PORTS = {"https": 443, "ssh": 22, "http": 80}
_ENCODED_DELIMITER = re.compile(r"%(?:00|23|25|2f|3f|5c)", re.IGNORECASE)
_CONTROL = re.compile(r"[\x00-\x20\x7f]")


class RepositorySourcePolicyError(ValueError):
    def __init__(self, reason: str = "repository_source_not_allowed"):
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class NormalizedRemoteSource:
    source_type: str
    locator: str
    scheme: str
    host: str
    port: int
    path: str
    origin: str
    requires_internal_address: bool


def _canonical_host(hostname: str) -> str:
    host = hostname.rstrip(".").lower()
    if not host or "%" in host or _CONTROL.search(host):
        raise RepositorySourcePolicyError()
    try:
        return host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise RepositorySourcePolicyError() from exc


def _origin(scheme: str, host: str, port: int) -> str:
    display_host = f"[{host}]" if ":" in host else host
    return f"{scheme}://{display_host}:{port}"


def _canonical_path(raw_path: str) -> str:
    if not raw_path or not raw_path.startswith("/") or _CONTROL.search(raw_path):
        raise RepositorySourcePolicyError()
    if _ENCODED_DELIMITER.search(raw_path):
        raise RepositorySourcePolicyError()
    try:
        decoded = unquote(raw_path, errors="strict")
    except (UnicodeDecodeError, ValueError) as exc:
        raise RepositorySourcePolicyError() from exc
    if _CONTROL.search(decoded) or "\\" in decoded or "//" in decoded:
        raise RepositorySourcePolicyError()
    segments = decoded.split("/")[1:]
    if not segments or any(segment in {"", ".", ".."} for segment in segments):
        raise RepositorySourcePolicyError()
    canonical = quote(decoded, safe="/:@!$&'()*+,;=-._~")
    return canonical.rstrip("/")


def _parse_remote(value: str, *, require_path: bool) -> tuple[str, str, int, str]:
    raw = str(value or "")
    if not raw or raw != raw.strip() or _CONTROL.search(raw) or "\\" in raw:
        raise RepositorySourcePolicyError()
    try:
        parsed = urlsplit(raw)
        scheme = parsed.scheme.lower()
        port = parsed.port
    except ValueError as exc:
        raise RepositorySourcePolicyError() from exc
    if scheme not in _DEFAULT_PORTS or not parsed.hostname:
        raise RepositorySourcePolicyError()
    if parsed.username is not None or parsed.password is not None:
        raise RepositorySourcePolicyError()
    if parsed.query or parsed.fragment:
        raise RepositorySourcePolicyError()
    host = _canonical_host(parsed.hostname)
    resolved_port = _DEFAULT_PORTS[scheme] if port is None else port
    if not 1 <= resolved_port <= 65535:
        raise RepositorySourcePolicyError()
    if require_path:
        path = _canonical_path(parsed.path)
    else:
        if parsed.path not in {"", "/"} or port is None:
            raise RepositorySourcePolicyError()
        path = ""
    return scheme, host, resolved_port, path


def normalize_repository_allowlist(raw: str | list[str]) -> frozenset[str]:
    if isinstance(raw, str):
        try:
            values = json.loads(raw or "[]")
        except json.JSONDecodeError as exc:
            raise RepositorySourcePolicyError() from exc
    else:
        values = raw
    if not isinstance(values, list) or not values:
        raise RepositorySourcePolicyError()
    origins: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            raise RepositorySourcePolicyError()
        scheme, host, port, _ = _parse_remote(value, require_path=False)
        origins.add(_origin(scheme, host, port))
    return frozenset(origins)


def normalize_remote_source(
    locator: str,
    *,
    allowlist: str | list[str],
) -> NormalizedRemoteSource:
    """Validate a remote source without DNS lookup or any network request."""
    allowed_origins = normalize_repository_allowlist(allowlist)
    normalized = normalize_remote_source_syntax(locator)
    if normalized.origin not in allowed_origins:
        raise RepositorySourcePolicyError()
    return normalized


def normalize_remote_source_syntax(locator: str) -> NormalizedRemoteSource:
    """Normalize a remote source URL without applying deployment allowlists."""
    scheme, host, port, path = _parse_remote(locator, require_path=True)
    origin = _origin(scheme, host, port)
    return NormalizedRemoteSource(
        source_type=scheme,
        locator=origin + path,
        scheme=scheme,
        host=host,
        port=port,
        path=path,
        origin=origin,
        requires_internal_address=scheme == "http",
    )
