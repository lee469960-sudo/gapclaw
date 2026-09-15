import concurrent.futures
import threading
import time

from app.services import docker_service
from app.services.agent_runtime.runtime import _tool_result_failed


class _ConflictError(Exception):
    status_code = 409


class _Container:
    def __init__(self, *, status="running", failures=0, delay=0.0):
        self.status = status
        self.failures = failures
        self.delay = delay
        self.calls = 0
        self.starts = 0
        self.unpauses = 0
        self.active = 0
        self.max_active = 0
        self._guard = threading.Lock()

    def reload(self):
        return None

    def start(self):
        self.starts += 1
        self.status = "running"

    def unpause(self):
        self.unpauses += 1
        self.status = "running"

    def exec_run(self, _command, demux=False):
        assert demux is False
        with self._guard:
            self.calls += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            should_fail = self.calls <= self.failures
        try:
            if self.delay:
                time.sleep(self.delay)
            if should_fail:
                self.status = "exited"
                raise _ConflictError("409 Client Error: container is not running")
            return 0, b"ok"
        finally:
            with self._guard:
                self.active -= 1


class _Containers:
    def __init__(self, container):
        self.container = container

    def get(self, _container_id):
        return self.container


class _Client:
    def __init__(self, container):
        self.containers = _Containers(container)


def _use_container(monkeypatch, container):
    docker_service._exec_locks.clear()
    monkeypatch.setattr(docker_service, "get_docker_client", lambda: _Client(container))


def test_exec_in_sandbox_serializes_calls_for_the_same_container(monkeypatch):
    container = _Container(delay=0.03)
    _use_container(monkeypatch, container)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(
            lambda command: docker_service.exec_in_sandbox("sandbox-serial", command),
            ["echo one", "echo two"],
        ))

    assert results == ["ok", "ok"]
    assert container.max_active == 1


def test_exec_in_sandbox_recovers_stopped_container_before_exec(monkeypatch):
    container = _Container(status="exited")
    _use_container(monkeypatch, container)

    result = docker_service.exec_in_sandbox("sandbox-stopped", "echo ok")

    assert result == "ok"
    assert container.starts == 1
    assert container.calls == 1


def test_exec_in_sandbox_recovers_and_retries_one_409(monkeypatch):
    container = _Container(failures=1)
    _use_container(monkeypatch, container)

    result = docker_service.exec_in_sandbox("sandbox-retry", "echo ok")

    assert result == "ok"
    assert container.starts == 1
    assert container.calls == 2


def test_exec_in_sandbox_bounds_persistent_409(monkeypatch):
    container = _Container(failures=2)
    _use_container(monkeypatch, container)

    result = docker_service.exec_in_sandbox("sandbox-conflict", "echo ok")

    assert result.startswith("[sandbox_unavailable]")
    assert "409 Client Error" not in result
    assert container.calls == 2
    assert _tool_result_failed(result) is True
