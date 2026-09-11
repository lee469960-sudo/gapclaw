"""mTLS configuration and fixed HTTP operation routing for Deploy Runner."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import ssl
from typing import Any, Protocol
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json

from tools.gap_deploy_runner.runner import DeployRunner, RunnerCommandError
from tools.gap_deploy_runner.state import ReleaseStateStore


@dataclass(frozen=True)
class MtlsFiles:
    ca_file: Path
    cert_file: Path
    key_file: Path

    def server_context(self) -> ssl.SSLContext:
        context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        context.load_verify_locations(cafile=str(self.ca_file))
        context.load_cert_chain(certfile=str(self.cert_file), keyfile=str(self.key_file))
        context.verify_mode = ssl.CERT_REQUIRED
        return context


class CallbackRetry(Protocol):
    def retry_pending(self) -> dict[str, int]: ...


class RunnerHttpApi:
    """The only network-visible Runner operations; deploy remains local CLI-only."""

    def __init__(
        self,
        runner: DeployRunner,
        *,
        callbacks: CallbackRetry | None = None,
        state_store: ReleaseStateStore | None = None,
    ):
        self.runner, self.callbacks, self.state_store = runner, callbacks, state_store

    def handle(self, method: str, path: str) -> tuple[int, dict[str, Any]]:
        if self.callbacks is not None:
            self.callbacks.retry_pending()
        routes = {("GET", "/v1/status"): "status", ("GET", "/v1/health"): "health", ("POST", "/v1/rollback"): "rollback"}
        operation = routes.get((method, path))
        if operation is None:
            return 404, {"reason": "runner_endpoint_not_found"}
        try:
            result = self.runner.dispatch(operation)
            if operation == "status" and self.state_store is not None:
                baseline = self.state_store.load()["last_known_healthy"]
                result["last_known_healthy"] = {
                    "release_id": baseline["release_id"], "target_id": baseline["target_id"],
                } if isinstance(baseline, dict) else None
            return 200, result
        except RunnerCommandError as exc:
            return 409, {"reason": exc.reason}


def create_server(address: tuple[str, int], api: RunnerHttpApi, mtls: MtlsFiles) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None: self._reply(*api.handle("GET", self.path))
        def do_POST(self) -> None: self._reply(*api.handle("POST", self.path))
        def log_message(self, *_args: object) -> None: pass
        def _reply(self, status: int, body: dict[str, Any]) -> None:
            raw = json.dumps(body).encode()
            self.send_response(status); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
    server = ThreadingHTTPServer(address, Handler)
    server.socket = mtls.server_context().wrap_socket(server.socket, server_side=True)
    return server
