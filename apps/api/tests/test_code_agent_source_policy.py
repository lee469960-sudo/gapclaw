"""OpenSpec task 3.1: exact remote source normalization and pre-connect policy."""

from __future__ import annotations

import socket

import pytest

from app.services.code_agent.source_policy import (
    RepositorySourcePolicyError,
    normalize_remote_source,
    normalize_repository_allowlist,
)


@pytest.mark.parametrize(
    ("locator", "allowlist", "expected"),
    [
        (
            "HTTPS://Git.Example.Test./system/%72epo.git",
            ["https://git.example.test:443"],
            "https://git.example.test:443/system/repo.git",
        ),
        (
            "ssh://git.example.test/system/repo.git",
            ["ssh://git.example.test:22"],
            "ssh://git.example.test:22/system/repo.git",
        ),
        (
            "http://g.testskydata.com:80/system/dbt-gamestat-ck.git",
            ["http://g.testskydata.com:80"],
            "http://g.testskydata.com:80/system/dbt-gamestat-ck.git",
        ),
    ],
)
def test_approved_sources_are_canonicalized_by_scheme_host_port_and_path(
    locator,
    allowlist,
    expected,
):
    source = normalize_remote_source(locator, allowlist=allowlist)

    assert source.locator == expected
    assert source.origin in normalize_repository_allowlist(allowlist)
    assert source.requires_internal_address is (source.scheme == "http")


@pytest.mark.parametrize(
    "locator",
    [
        " https://git.example.test/repo.git",
        "https://git.example.test/repo.git ",
        "https://user@git.example.test/repo.git",
        "https://user:password@git.example.test/repo.git",
        "https://user%40name@git.example.test/repo.git",
        "file:///srv/repo",
        "git://git.example.test/repo.git",
        "ftp://git.example.test/repo.git",
        "git@git.example.test:repo.git",
    ],
)
def test_credentials_file_scheme_and_unsupported_transports_are_rejected(locator):
    with pytest.raises(RepositorySourcePolicyError, match="repository_source_not_allowed"):
        normalize_remote_source(
            locator,
            allowlist=["https://git.example.test:443"],
        )


@pytest.mark.parametrize(
    ("locator", "allowlist"),
    [
        ("https://other.example.test/repo.git", ["https://git.example.test:443"]),
        ("https://git.example.test:8443/repo.git", ["https://git.example.test:443"]),
        ("https://git.example.test:0/repo.git", ["https://git.example.test:443"]),
        ("ssh://git.example.test/repo.git", ["https://git.example.test:443"]),
        ("http://git.example.test/repo.git", ["https://git.example.test:443"]),
        ("https://git.example.test/repo.git", ["https://git.example.test:8443"]),
    ],
)
def test_unapproved_scheme_host_or_effective_port_is_rejected(locator, allowlist):
    with pytest.raises(RepositorySourcePolicyError, match="repository_source_not_allowed"):
        normalize_remote_source(locator, allowlist=allowlist)


@pytest.mark.parametrize(
    "locator",
    [
        "https://git.example.test",
        "https://git.example.test/",
        "https://git.example.test//repo.git",
        "https://git.example.test/../repo.git",
        "https://git.example.test/%2e%2e/repo.git",
        "https://git.example.test/repo%2fgit",
        "https://git.example.test/repo%5cgit",
        "https://git.example.test/repo%2500git",
        "https://git.example.test/repo.git?token=value",
        "https://git.example.test/repo.git#fragment",
        "https://git.example.test/repo.git\x00suffix",
    ],
)
def test_path_query_fragment_and_encoding_ambiguities_are_rejected(locator):
    with pytest.raises(RepositorySourcePolicyError, match="repository_source_not_allowed"):
        normalize_remote_source(
            locator,
            allowlist=["https://git.example.test:443"],
        )


@pytest.mark.parametrize(
    "entry",
    [
        "https://git.example.test",
        "https://git.example.test:443/repositories",
        "https://user@git.example.test:443",
        "file://localhost:1",
        "http://git.example.test:99999",
        "https://git.example.test:0",
    ],
)
def test_allowlist_requires_credential_free_exact_origin_with_explicit_port(entry):
    with pytest.raises(RepositorySourcePolicyError, match="repository_source_not_allowed"):
        normalize_repository_allowlist([entry])


def test_normalization_never_performs_dns_or_network_io(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("pre-connect normalization performed network I/O")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)

    source = normalize_remote_source(
        "https://git.example.test/repo.git",
        allowlist=["https://git.example.test:443"],
    )

    assert source.host == "git.example.test"
