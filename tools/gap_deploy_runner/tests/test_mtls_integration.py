from __future__ import annotations

import socket
import ssl
import subprocess
import threading
from pathlib import Path

import pytest

from tools.gap_deploy_runner.mtls import MtlsFiles, RunnerHttpApi, create_server
from tools.gap_deploy_runner.runner import DeployRunner


def _openssl(*args: str) -> None:
    subprocess.run(
        ["openssl", *args],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _issue_certificate(
    directory: Path,
    name: str,
    ca_cert: Path,
    ca_key: Path,
    purpose: str,
) -> tuple[Path, Path]:
    key = directory / f"{name}.key"
    request = directory / f"{name}.csr"
    certificate = directory / f"{name}.crt"
    extensions = directory / f"{name}.ext"
    extensions.write_text(
        "\n".join(
            (
                "basicConstraints=critical,CA:FALSE",
                "subjectKeyIdentifier=hash",
                "authorityKeyIdentifier=keyid,issuer",
                f"extendedKeyUsage={purpose}",
            )
        ),
        encoding="utf-8",
    )
    _openssl(
        "req",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-keyout",
        str(key),
        "-out",
        str(request),
        "-subj",
        f"/CN={name}",
    )
    _openssl(
        "x509",
        "-req",
        "-in",
        str(request),
        "-CA",
        str(ca_cert),
        "-CAkey",
        str(ca_key),
        "-CAcreateserial",
        "-extfile",
        str(extensions),
        "-out",
        str(certificate),
        "-days",
        "1",
    )
    return certificate, key


def _certificate_files(directory: Path) -> tuple[Path, Path, Path, Path, Path]:
    ca_key = directory / "ca.key"
    ca_cert = directory / "ca.crt"
    _openssl(
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-keyout",
        str(ca_key),
        "-out",
        str(ca_cert),
        "-subj",
        "/CN=gap-test-ca",
        "-addext",
        "basicConstraints=critical,CA:TRUE",
        "-addext",
        "keyUsage=critical,keyCertSign,cRLSign",
        "-addext",
        "subjectKeyIdentifier=hash",
        "-days",
        "1",
    )
    server_cert, server_key = _issue_certificate(directory, "runner", ca_cert, ca_key, "serverAuth")
    client_cert, client_key = _issue_certificate(directory, "gap-client", ca_cert, ca_key, "clientAuth")
    return ca_cert, server_cert, server_key, client_cert, client_key


def _request(context: ssl.SSLContext, address: tuple[str, int], request: bytes | None = None) -> bytes:
    with socket.create_connection(address, timeout=2) as connection:
        with context.wrap_socket(connection, server_hostname="runner") as tls_connection:
            tls_connection.sendall(request or b"GET /v1/status HTTP/1.1\r\nHost: runner\r\nConnection: close\r\n\r\n")
            return tls_connection.recv(4096)


def test_mtls_server_accepts_trusted_client_and_rejects_client_without_certificate(tmp_path):
    ca_cert, server_cert, server_key, client_cert, client_key = _certificate_files(tmp_path)
    runner = DeployRunner(
        allowed_images={"api": "registry.example.com/gap-api", "web": "registry.example.com/gap-web"},
        target_id="production",
    )
    server = create_server(
        ("127.0.0.1", 0),
        RunnerHttpApi(runner),
        MtlsFiles(ca_file=ca_cert, cert_file=server_cert, key_file=server_key),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        trusted = ssl.create_default_context(cafile=str(ca_cert))
        trusted.load_cert_chain(certfile=str(client_cert), keyfile=str(client_key))
        assert b"200" in _request(trusted, server.server_address)

        untrusted = ssl.create_default_context(cafile=str(ca_cert))
        with pytest.raises(ssl.SSLError):
            _request(untrusted, server.server_address)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_mtls_server_allows_only_gap_client_identity_to_deploy(tmp_path):
    ca_cert, server_cert, server_key, client_cert, client_key = _certificate_files(tmp_path)
    other_cert, other_key = _issue_certificate(tmp_path, "other-client", ca_cert, tmp_path / "ca.key", "clientAuth")
    runner = DeployRunner(
        allowed_images={"api": "registry.example.com/gap-api", "web": "registry.example.com/gap-web"},
        target_id="production",
    )
    server = create_server(
        ("127.0.0.1", 0),
        RunnerHttpApi(runner),
        MtlsFiles(ca_file=ca_cert, cert_file=server_cert, key_file=server_key),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    payload = (
        b'{"api_image":"registry.example.com/gap-api@sha256:' + b"a" * 64
        + b'","commit_sha":"0123456789abcdef0123456789abcdef01234567","created_at":"2026-01-01T00:00:00Z",'
        + b'"git_tag":"v1.2.1","health_check_version":"v1","release_id":"v1.2.1-01234567",'
        + b'"schema_version":1,"target_id":"production","version":"v1.2.1",'
        + b'"web_image":"registry.example.com/gap-web@sha256:' + b"b" * 64 + b'"}'
    )
    request = (
        b"POST /v1/deploy HTTP/1.1\r\nHost: runner\r\nContent-Type: application/json\r\nContent-Length: "
        + str(len(payload)).encode() + b"\r\nConnection: close\r\n\r\n" + payload
    )
    try:
        trusted = ssl.create_default_context(cafile=str(ca_cert))
        trusted.load_cert_chain(certfile=str(client_cert), keyfile=str(client_key))
        arbitrary = b'{"command":"docker compose down"}'
        arbitrary_request = (
            b"POST /v1/deploy HTTP/1.1\r\nHost: runner\r\nContent-Type: application/json\r\nContent-Length: "
            + str(len(arbitrary)).encode() + b"\r\nConnection: close\r\n\r\n" + arbitrary
        )
        assert b"409" in _request(trusted, server.server_address, arbitrary_request)
        assert runner.dispatch("status")["release"]["release_id"] == ""
        assert b"200" in _request(trusted, server.server_address, request)

        other = ssl.create_default_context(cafile=str(ca_cert))
        other.load_cert_chain(certfile=str(other_cert), keyfile=str(other_key))
        assert b"403" in _request(other, server.server_address, request)
        assert runner.dispatch("status")["release"]["release_id"] == "v1.2.1-01234567"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
