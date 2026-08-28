"""Per-connection DNS/IP and redirect policy for repository imports."""

from __future__ import annotations

import ipaddress
import json
import socket
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

from app.services.code_agent.source_policy import (
    NormalizedRemoteSource,
    RepositorySourcePolicyError,
    normalize_remote_source,
)


_ADMIN_APPROVABLE_INTERNAL_RANGES = tuple(
    ipaddress.ip_network(value)
    for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")
)
_METADATA_ADDRESSES = frozenset({
    ipaddress.ip_address("169.254.169.254"),
    ipaddress.ip_address("100.100.100.200"),
    ipaddress.ip_address("fe80::a9fe:a9fe"),
})
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


class RepositoryNetworkPolicyError(RuntimeError):
    def __init__(self, reason: str = "repository_network_policy_denied"):
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class ValidatedRepositoryConnection:
    """A source plus approved transport metadata for repository access."""

    source: NormalizedRemoteSource
    addresses: tuple[str, ...]
    contains_approved_internal_address: bool
    uses_transport_proxy: bool = False


@dataclass(frozen=True)
class RepositoryTransportResponse:
    status_code: int
    redirect_location: str = ""
    payload: Any = None


def normalize_approved_internal_cidrs(
    raw: str | list[str],
) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    if isinstance(raw, str):
        try:
            values = json.loads(raw or "[]")
        except json.JSONDecodeError as exc:
            raise RepositoryNetworkPolicyError() from exc
    else:
        values = raw
    if not isinstance(values, list):
        raise RepositoryNetworkPolicyError()

    networks: set[ipaddress.IPv4Network | ipaddress.IPv6Network] = set()
    for value in values:
        if not isinstance(value, str) or value != value.strip():
            raise RepositoryNetworkPolicyError()
        try:
            network = ipaddress.ip_network(value, strict=True)
        except ValueError as exc:
            raise RepositoryNetworkPolicyError() from exc
        if not any(
            network.version == parent.version and network.subnet_of(parent)
            for parent in _ADMIN_APPROVABLE_INTERNAL_RANGES
        ):
            raise RepositoryNetworkPolicyError()
        networks.add(network)
    return tuple(sorted(networks, key=lambda item: (item.version, int(item.network_address), item.prefixlen)))


class RepositoryNetworkGuard:
    """Resolve and approve each connection immediately before transport use."""

    def __init__(
        self,
        *,
        allowlist: str | list[str],
        approved_internal_cidrs: str | list[str] = "[]",
        resolver: Callable[..., Iterable[tuple]] | None = None,
        max_redirects: int = 5,
        use_transport_proxy: bool = False,
        allow_public_http: bool = False,
    ):
        if (
            not isinstance(max_redirects, int)
            or isinstance(max_redirects, bool)
            or max_redirects < 0
        ):
            raise RepositoryNetworkPolicyError()
        self.allowlist = allowlist
        self.approved_internal_networks = normalize_approved_internal_cidrs(
            approved_internal_cidrs
        )
        self.resolver = resolver or socket.getaddrinfo
        self.max_redirects = max_redirects
        self.use_transport_proxy = bool(use_transport_proxy)
        self.allow_public_http = bool(allow_public_http)

    def normalize_source(
        self,
        source: str | NormalizedRemoteSource,
    ) -> NormalizedRemoteSource:
        locator = source.locator if isinstance(source, NormalizedRemoteSource) else source
        try:
            return normalize_remote_source(locator, allowlist=self.allowlist)
        except RepositorySourcePolicyError as exc:
            raise RepositoryNetworkPolicyError("repository_source_not_allowed") from exc

    def _approved_internal(
        self,
        address: ipaddress.IPv4Address | ipaddress.IPv6Address,
    ) -> bool:
        return any(
            address.version == network.version and address in network
            for network in self.approved_internal_networks
        )

    @staticmethod
    def _always_forbidden(
        address: ipaddress.IPv4Address | ipaddress.IPv6Address,
    ) -> bool:
        return bool(
            address in _METADATA_ADDRESSES
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_unspecified
            or address.is_reserved
        )

    def validate_connection(
        self,
        source: str | NormalizedRemoteSource,
    ) -> ValidatedRepositoryConnection:
        normalized = self.normalize_source(source)
        if self.use_transport_proxy and normalized.scheme in {"http", "https"}:
            return ValidatedRepositoryConnection(
                source=normalized,
                addresses=(),
                contains_approved_internal_address=False,
                uses_transport_proxy=True,
            )
        try:
            answers = tuple(self.resolver(
                normalized.host,
                normalized.port,
                type=socket.SOCK_STREAM,
                proto=socket.IPPROTO_TCP,
            ))
        except (OSError, ValueError, TypeError) as exc:
            raise RepositoryNetworkPolicyError("repository_unreachable") from exc
        if not answers:
            raise RepositoryNetworkPolicyError("repository_unreachable")

        addresses: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
        try:
            for answer in answers:
                sockaddr = answer[4]
                raw_address = str(sockaddr[0]).split("%", 1)[0]
                addresses.add(ipaddress.ip_address(raw_address))
        except (IndexError, TypeError, ValueError) as exc:
            raise RepositoryNetworkPolicyError("repository_network_policy_denied") from exc
        if not addresses:
            raise RepositoryNetworkPolicyError("repository_unreachable")

        contains_internal = False
        for address in addresses:
            if self._always_forbidden(address):
                raise RepositoryNetworkPolicyError()
            approved_internal = self._approved_internal(address)
            if not address.is_global and not approved_internal:
                raise RepositoryNetworkPolicyError()
            public_http_allowed = (
                normalized.requires_internal_address
                and self.allow_public_http
                and address.is_global
            )
            if (
                normalized.requires_internal_address
                and not approved_internal
                and not public_http_allowed
            ):
                raise RepositoryNetworkPolicyError()
            contains_internal = contains_internal or approved_internal

        return ValidatedRepositoryConnection(
            source=normalized,
            addresses=tuple(sorted(str(address) for address in addresses)),
            contains_approved_internal_address=contains_internal,
        )

    def _normalize_redirect(
        self,
        current: NormalizedRemoteSource,
        location: str,
    ) -> NormalizedRemoteSource:
        try:
            return normalize_remote_source(
                urljoin(current.locator, location),
                allowlist=self.allowlist,
            )
        except RepositorySourcePolicyError as exc:
            raise RepositoryNetworkPolicyError() from exc

    def follow_redirects(
        self,
        source: str | NormalizedRemoteSource,
        request: Callable[[ValidatedRepositoryConnection], RepositoryTransportResponse],
    ) -> RepositoryTransportResponse:
        current = self.normalize_source(source)
        redirect_count = 0
        while True:
            connection = self.validate_connection(current)
            response = request(connection)
            if not isinstance(response, RepositoryTransportResponse):
                raise RepositoryNetworkPolicyError("repository_unreachable")
            if response.status_code not in _REDIRECT_STATUSES:
                return response
            if not response.redirect_location or current.scheme not in {"http", "https"}:
                raise RepositoryNetworkPolicyError()
            if redirect_count >= self.max_redirects:
                raise RepositoryNetworkPolicyError()
            redirect_count += 1
            current = self._normalize_redirect(current, response.redirect_location)
