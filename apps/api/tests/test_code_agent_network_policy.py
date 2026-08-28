"""OpenSpec task 3.2: per-connection DNS/IP and redirect enforcement."""

from __future__ import annotations

import socket

import pytest

from app.services.code_agent.network_policy import (
    RepositoryNetworkGuard,
    RepositoryNetworkPolicyError,
    RepositoryTransportResponse,
)


HTTPS_ALLOWLIST = [
    "https://git.example.test:443",
    "https://mirror.example.test:443",
]


def _resolver(records, calls=None):
    calls = calls if calls is not None else []

    def resolve(host, port, **_kwargs):
        calls.append((host, port))
        value = records[host]
        addresses = value() if callable(value) else value
        return [
            (
                socket.AF_INET6 if ":" in address else socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                (address, port),
            )
            for address in addresses
        ]

    return resolve


def test_public_https_connection_returns_only_pinned_validated_addresses():
    guard = RepositoryNetworkGuard(
        allowlist=HTTPS_ALLOWLIST,
        resolver=_resolver({"git.example.test": ["93.184.216.34"]}),
    )

    connection = guard.validate_connection("https://git.example.test/repo.git")

    assert connection.source.origin == "https://git.example.test:443"
    assert connection.addresses == ("93.184.216.34",)
    assert connection.contains_approved_internal_address is False


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "169.254.169.254",
        "10.20.1.8",
        "192.168.1.10",
        "100.100.100.200",
        "0.0.0.0",
        "224.0.0.1",
        "::1",
        "fe80::1",
    ],
)
def test_loopback_link_local_metadata_private_and_special_addresses_are_denied(address):
    guard = RepositoryNetworkGuard(
        allowlist=HTTPS_ALLOWLIST,
        resolver=_resolver({"git.example.test": [address]}),
    )

    with pytest.raises(RepositoryNetworkPolicyError, match="repository_network_policy_denied"):
        guard.validate_connection("https://git.example.test/repo.git")


@pytest.mark.parametrize("scheme", ["https", "ssh", "http"])
def test_explicit_private_range_allows_only_matching_internal_address(scheme):
    port = {"https": 443, "ssh": 22, "http": 80}[scheme]
    guard = RepositoryNetworkGuard(
        allowlist=[f"{scheme}://git.internal.test:{port}"],
        approved_internal_cidrs=["10.20.0.0/16"],
        resolver=_resolver({"git.internal.test": ["10.20.1.8"]}),
    )

    connection = guard.validate_connection(f"{scheme}://git.internal.test/repo.git")

    assert connection.addresses == ("10.20.1.8",)
    assert connection.contains_approved_internal_address is True


def test_http_is_denied_when_exact_host_resolves_to_public_address():
    guard = RepositoryNetworkGuard(
        allowlist=["http://git.example.test:80"],
        approved_internal_cidrs=["10.20.0.0/16"],
        resolver=_resolver({"git.example.test": ["93.184.216.34"]}),
    )

    with pytest.raises(RepositoryNetworkPolicyError, match="repository_network_policy_denied"):
        guard.validate_connection("http://git.example.test/repo.git")


def test_public_http_requires_explicit_opt_in_even_when_origin_is_allowlisted():
    guard = RepositoryNetworkGuard(
        allowlist=["http://git.example.test:80"],
        allow_public_http=True,
        resolver=_resolver({"git.example.test": ["93.184.216.34"]}),
    )

    connection = guard.validate_connection("http://git.example.test/repo.git")

    assert connection.addresses == ("93.184.216.34",)
    assert connection.contains_approved_internal_address is False


def test_one_disallowed_dns_answer_rejects_the_entire_connection():
    guard = RepositoryNetworkGuard(
        allowlist=HTTPS_ALLOWLIST,
        resolver=_resolver({
            "git.example.test": ["93.184.216.34", "127.0.0.1"],
        }),
    )

    with pytest.raises(RepositoryNetworkPolicyError, match="repository_network_policy_denied"):
        guard.validate_connection("https://git.example.test/repo.git")


def test_dns_is_resolved_again_for_every_connection_to_block_rebinding():
    calls = []
    answers = iter([["93.184.216.34"], ["10.99.0.8"]])
    guard = RepositoryNetworkGuard(
        allowlist=HTTPS_ALLOWLIST,
        resolver=_resolver({"git.example.test": lambda: next(answers)}, calls),
    )

    guard.validate_connection("https://git.example.test/repo.git")
    with pytest.raises(RepositoryNetworkPolicyError, match="repository_network_policy_denied"):
        guard.validate_connection("https://git.example.test/repo.git")

    assert calls == [
        ("git.example.test", 443),
        ("git.example.test", 443),
    ]


def test_dns_failure_is_stable_and_no_transport_is_called():
    def unavailable(*_args, **_kwargs):
        raise socket.gaierror("sensitive resolver detail")

    guard = RepositoryNetworkGuard(
        allowlist=HTTPS_ALLOWLIST,
        resolver=unavailable,
    )
    transport_calls = []

    with pytest.raises(RepositoryNetworkPolicyError, match="repository_unreachable"):
        guard.follow_redirects(
            "https://git.example.test/repo.git",
            lambda connection: transport_calls.append(connection),
        )

    assert transport_calls == []


def test_trusted_transport_proxy_mode_skips_local_dns_for_http_source():
    def unavailable(*_args, **_kwargs):
        raise socket.gaierror("local dns unavailable")

    guard = RepositoryNetworkGuard(
        allowlist=["http://g.testskydata.com:2222"],
        resolver=unavailable,
        use_transport_proxy=True,
    )

    connection = guard.validate_connection(
        "http://g.testskydata.com:2222/system/dbt-gamestat-ck.git"
    )

    assert connection.source.origin == "http://g.testskydata.com:2222"
    assert connection.addresses == ()
    assert connection.uses_transport_proxy is True


def test_trusted_transport_proxy_mode_still_requires_source_allowlist():
    guard = RepositoryNetworkGuard(
        allowlist=["http://g.testskydata.com:2222"],
        use_transport_proxy=True,
    )

    with pytest.raises(RepositoryNetworkPolicyError, match="repository_source_not_allowed"):
        guard.validate_connection("http://evil.test:2222/private.git")


def test_allowlist_escape_redirect_is_rejected_before_dns_or_second_request():
    dns_calls = []
    request_calls = []
    guard = RepositoryNetworkGuard(
        allowlist=HTTPS_ALLOWLIST,
        resolver=_resolver({"git.example.test": ["93.184.216.34"]}, dns_calls),
    )

    def request(connection):
        request_calls.append(connection.source.host)
        return RepositoryTransportResponse(302, "https://evil.test/private.git")

    with pytest.raises(RepositoryNetworkPolicyError, match="repository_network_policy_denied"):
        guard.follow_redirects("https://git.example.test/repo.git", request)

    assert request_calls == ["git.example.test"]
    assert dns_calls == [("git.example.test", 443)]


def test_redirect_to_allowlisted_but_private_target_is_rejected_before_request():
    request_calls = []
    guard = RepositoryNetworkGuard(
        allowlist=HTTPS_ALLOWLIST,
        resolver=_resolver({
            "git.example.test": ["93.184.216.34"],
            "mirror.example.test": ["10.99.0.9"],
        }),
    )

    def request(connection):
        request_calls.append(connection.source.host)
        return RepositoryTransportResponse(
            302,
            "https://mirror.example.test/repo.git",
        )

    with pytest.raises(RepositoryNetworkPolicyError, match="repository_network_policy_denied"):
        guard.follow_redirects("https://git.example.test/repo.git", request)

    assert request_calls == ["git.example.test"]


def test_each_approved_redirect_hop_is_resolved_and_passed_as_pinned_ip():
    dns_calls = []
    request_calls = []
    guard = RepositoryNetworkGuard(
        allowlist=HTTPS_ALLOWLIST,
        resolver=_resolver({
            "git.example.test": ["93.184.216.34"],
            "mirror.example.test": ["93.184.216.35"],
        }, dns_calls),
    )

    def request(connection):
        request_calls.append((connection.source.host, connection.addresses))
        if connection.source.host == "git.example.test":
            return RepositoryTransportResponse(
                307,
                "https://mirror.example.test/repo.git",
            )
        return RepositoryTransportResponse(200, payload=b"ok")

    response = guard.follow_redirects("https://git.example.test/repo.git", request)

    assert response.payload == b"ok"
    assert request_calls == [
        ("git.example.test", ("93.184.216.34",)),
        ("mirror.example.test", ("93.184.216.35",)),
    ]
    assert dns_calls == [
        ("git.example.test", 443),
        ("mirror.example.test", 443),
    ]


def test_same_host_redirect_re_resolves_and_blocks_rebinding_before_second_request():
    answers = iter([["93.184.216.34"], ["127.0.0.1"]])
    request_calls = []
    guard = RepositoryNetworkGuard(
        allowlist=HTTPS_ALLOWLIST,
        resolver=_resolver({"git.example.test": lambda: next(answers)}),
    )

    def request(connection):
        request_calls.append(connection.addresses)
        return RepositoryTransportResponse(302, "/next.git")

    with pytest.raises(RepositoryNetworkPolicyError, match="repository_network_policy_denied"):
        guard.follow_redirects("https://git.example.test/repo.git", request)

    assert request_calls == [("93.184.216.34",)]


def test_redirect_limit_fails_closed_without_an_extra_request():
    request_calls = []
    guard = RepositoryNetworkGuard(
        allowlist=HTTPS_ALLOWLIST,
        resolver=_resolver({"git.example.test": ["93.184.216.34"]}),
        max_redirects=1,
    )

    def request(connection):
        request_calls.append(connection.source.path)
        return RepositoryTransportResponse(302, "/next.git")

    with pytest.raises(RepositoryNetworkPolicyError, match="repository_network_policy_denied"):
        guard.follow_redirects("https://git.example.test/repo.git", request)

    assert request_calls == ["/repo.git", "/next.git"]
