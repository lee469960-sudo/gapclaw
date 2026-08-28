"""OpenSpec task 5.4: deploy-config validation for the Workspace dual-root mapping.

The Compose API service must declare the API-visible and daemon-host Workspace
roots such that both resolve to the same on-disk storage through the single data
bind mount (``${GAP_DATA_DIR:-./data}:/app/data``). This test reads the actual
``deploy/docker-compose.yml`` and asserts that consistency statically, without a
running container or Docker daemon.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_PATH = REPO_ROOT / "deploy" / "docker-compose.yml"
DATA_MOUNT_SOURCE = "${GAP_DATA_DIR:-./data}"
DATA_MOUNT_TARGET = "/app/data"


def _compose() -> dict:
    return yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))


def _api_env() -> dict[str, str]:
    environment = _compose()["services"]["api"]["environment"]
    assert isinstance(environment, dict), "api environment must be a mapping"
    return {str(k): str(v) for k, v in environment.items()}


def _data_mount() -> str:
    volumes = _compose()["services"]["api"]["volumes"]
    data_mounts = [v for v in volumes if str(v).endswith(f":{DATA_MOUNT_TARGET}")]
    assert data_mounts, f"api service must bind-mount {DATA_MOUNT_TARGET}"
    return str(data_mounts[0])


def test_compose_declares_same_source_workspace_dual_roots():
    env = _api_env()
    api_root = env["CODE_WORKSPACE_API_ROOT"]
    host_root = env["CODE_WORKSPACE_HOST_ROOT"]
    # Both roots must sit at the same relative tail (``code-agent/runs``).
    assert api_root == f"{DATA_MOUNT_TARGET}/code-agent/runs"
    assert host_root == f"{DATA_MOUNT_SOURCE}/code-agent/runs"


def test_compose_data_mount_links_dual_roots_to_same_storage():
    mount = _data_mount()
    source = mount.split(f":{DATA_MOUNT_TARGET}")[0]
    assert source == DATA_MOUNT_SOURCE

    env = _api_env()
    api_root = Path(env["CODE_WORKSPACE_API_ROOT"])
    host_root = env["CODE_WORKSPACE_HOST_ROOT"]
    # API root lives under the container mount target; the host root mirrors the
    # same tail under the host mount source, so both names point at one directory.
    api_tail = api_root.relative_to(DATA_MOUNT_TARGET)
    assert host_root == f"{source}/{api_tail}"


def test_compose_exposes_docker_data_host_path_for_runtime_probe():
    env = _api_env()
    assert env["DOCKER_DATA_HOST_PATH"] == "${DOCKER_DATA_HOST_PATH}"
    assert env["DATA_DIR"] == DATA_MOUNT_TARGET
