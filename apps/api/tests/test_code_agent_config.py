"""Deploy-time configuration gate for the secure CodeAgent runtime."""

from __future__ import annotations

import json

import pytest

from app.config import Settings


def _valid_settings(tmp_path, **overrides):
    data_root = tmp_path / "data"
    api_root = data_root / "code_agent" / "runs"
    api_root.mkdir(parents=True)
    values = {
        "data_dir": str(data_root),
        "code_repository_allowlist": json.dumps(["https://git.example.test:443"]),
        "code_local_repository_roots": "[]",
        "code_trusted_image_digests": json.dumps([
            "registry.example.test/code-runner@sha256:" + "a" * 64
        ]),
        "code_workspace_api_root": str(api_root),
        "code_workspace_host_root": "/srv/gap/code-agent/runs",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_complete_code_agent_security_config_is_ready(tmp_path):
    result = _valid_settings(tmp_path).code_agent_security_readiness()
    assert result == {
        "ready": True,
        "status": "ready",
        "reason": "ready",
        "errors": {},
    }


def test_missing_code_agent_security_config_fails_closed_without_raising(tmp_path):
    settings = Settings(_env_file=None, data_dir=str(tmp_path / "data"))
    result = settings.code_agent_security_readiness()
    assert result["ready"] is False
    assert result["reason"] == "code_agent_security_config_invalid"
    assert {
        "repository_sources", "workspace_api_root", "workspace_host_root",
        "trusted_image_digests",
    } <= set(result["errors"])


def test_workspace_api_root_cannot_escape_data_root(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    result = _valid_settings(
        tmp_path, code_workspace_api_root=str(outside)
    ).code_agent_security_readiness()
    assert result["ready"] is False
    assert result["errors"]["workspace_api_root"] == "code_config_workspace_api_root_invalid"


def _mapping_settings(tmp_path, *, host_root, data_host_path):
    data_root = tmp_path / "data"
    api_root = data_root / "code-agent" / "runs"
    api_root.mkdir(parents=True)
    return Settings(
        _env_file=None,
        data_dir=str(data_root),
        code_workspace_api_root=str(api_root),
        code_workspace_host_root=host_root,
        docker_data_host_path=data_host_path,
    )


def test_workspace_dual_roots_map_to_same_storage(tmp_path):
    result = _mapping_settings(
        tmp_path,
        host_root="/srv/gap/data/code-agent/runs",
        data_host_path="/srv/gap/data",
    ).code_agent_workspace_mapping_readiness()
    assert result == {"ready": True, "status": "ready", "reason": "ready", "errors": {}}


def test_workspace_dual_roots_pointing_to_different_storage_fail(tmp_path):
    result = _mapping_settings(
        tmp_path,
        host_root="/srv/gap/data/other/runs",
        data_host_path="/srv/gap/data",
    ).code_agent_workspace_mapping_readiness()
    assert result["ready"] is False
    assert result["errors"]["workspace_mapping"] == "code_config_workspace_roots_not_same_storage"


def test_workspace_host_root_outside_data_host_path_fail(tmp_path):
    result = _mapping_settings(
        tmp_path,
        host_root="/srv/gap/runs",
        data_host_path="/srv/gap/data",
    ).code_agent_workspace_mapping_readiness()
    assert result["ready"] is False
    assert result["errors"]["workspace_mapping"] == (
        "code_config_workspace_host_root_not_same_storage"
    )


def test_workspace_mapping_rejects_traversal_in_data_host_path(tmp_path):
    result = _mapping_settings(
        tmp_path,
        host_root="/srv/outside/code-agent/runs",
        data_host_path="/srv/../outside",
    ).code_agent_workspace_mapping_readiness()
    assert result["ready"] is False
    assert result["errors"]["workspace_mapping"] == (
        "code_config_workspace_data_host_path_invalid"
    )


def test_workspace_mapping_without_data_host_path_validates_format_only(tmp_path):
    result = _valid_settings(tmp_path).code_agent_workspace_mapping_readiness()
    assert result["ready"] is True


def test_allowlist_and_image_require_exact_ports_and_digests(tmp_path):
    result = _valid_settings(
        tmp_path,
        code_repository_allowlist=json.dumps([
            "https://user:password@git.example.test/path"
        ]),
        code_trusted_image_digests=json.dumps(["registry.example.test/code-runner:latest"]),
    ).code_agent_security_readiness()
    assert result["ready"] is False
    assert result["errors"]["repository_allowlist"] == (
        "code_config_invalid_repository_allowlist"
    )
    assert result["errors"]["trusted_image_digests"] == "code_config_invalid_image_digest"


def test_repository_allowlist_rejects_zero_port_default_bypass(tmp_path):
    result = _valid_settings(
        tmp_path,
        code_repository_allowlist=json.dumps(["https://git.example.test:0"]),
    ).code_agent_security_readiness()

    assert result["ready"] is False
    assert result["errors"]["repository_allowlist"] == (
        "code_config_invalid_repository_allowlist"
    )


def test_internal_http_requires_explicit_approved_internal_cidr(tmp_path):
    missing = _valid_settings(
        tmp_path / "missing",
        code_repository_allowlist=json.dumps(["http://git.internal.test:80"]),
    ).code_agent_security_readiness()
    approved = _valid_settings(
        tmp_path / "approved",
        code_repository_allowlist=json.dumps(["http://git.internal.test:80"]),
        code_repository_internal_cidrs=json.dumps(["10.20.0.0/16"]),
    ).code_agent_security_readiness()

    assert missing["errors"]["repository_internal_cidrs"] == (
        "code_config_internal_cidrs_required"
    )
    assert approved["ready"] is True


def test_public_http_can_use_explicit_opt_in_without_internal_cidr(tmp_path):
    result = _valid_settings(
        tmp_path,
        code_repository_allowlist=json.dumps(["http://g.testskydata.com:80"]),
        code_repository_allow_public_http=True,
    ).code_agent_security_readiness()

    assert result["ready"] is True


def test_internal_http_can_use_explicit_transport_proxy_without_internal_cidr(tmp_path):
    result = _valid_settings(
        tmp_path,
        code_repository_allowlist=json.dumps(["http://g.testskydata.com:2222"]),
        code_repository_use_transport_proxy=True,
        code_repository_http_proxy="http://127.0.0.1:7897",
    ).code_agent_security_readiness()

    assert result["ready"] is True


def test_transport_proxy_mode_requires_matching_proxy_url(tmp_path):
    result = _valid_settings(
        tmp_path,
        code_repository_allowlist=json.dumps(["http://g.testskydata.com:2222"]),
        code_repository_use_transport_proxy=True,
    ).code_agent_security_readiness()

    assert result["ready"] is False
    assert result["errors"]["repository_http_proxy"] == (
        "code_config_repository_http_proxy_missing"
    )


def test_ssh_source_requires_absolute_managed_known_hosts_file(tmp_path):
    missing = _valid_settings(
        tmp_path / "missing",
        code_repository_allowlist=json.dumps(["ssh://git.example.test:22"]),
    ).code_agent_security_readiness()
    approved_root = tmp_path / "approved"
    approved_root.mkdir()
    known_hosts = approved_root / "known_hosts"
    known_hosts.write_text(
        "git.example.test ssh-ed25519 AAAATEST\n",
        encoding="utf-8",
    )
    approved = _valid_settings(
        approved_root,
        code_repository_allowlist=json.dumps(["ssh://git.example.test:22"]),
        code_repository_ssh_known_hosts_file=str(known_hosts),
    ).code_agent_security_readiness()

    assert missing["errors"]["repository_ssh_known_hosts_file"] == (
        "code_config_ssh_known_hosts_file_unavailable"
    )
    assert approved["ready"] is True


@pytest.mark.parametrize("cidr", ["0.0.0.0/0", "127.0.0.0/8", "169.254.0.0/16", "not-a-cidr"])
def test_internal_cidr_configuration_rejects_non_internal_or_ambiguous_ranges(
    tmp_path,
    cidr,
):
    result = _valid_settings(
        tmp_path,
        code_repository_internal_cidrs=json.dumps([cidr]),
    ).code_agent_security_readiness()

    assert result["errors"]["repository_internal_cidrs"] == (
        "code_config_invalid_internal_cidrs"
    )


def test_local_repository_roots_must_exist_and_not_be_symlinks(tmp_path):
    source_root = tmp_path / "source"
    source_root.mkdir()
    link = tmp_path / "source-link"
    link.symlink_to(source_root, target_is_directory=True)
    result = _valid_settings(
        tmp_path,
        code_repository_allowlist="[]",
        code_local_repository_roots=json.dumps([str(link)]),
    ).code_agent_security_readiness()
    assert result["ready"] is False
    assert result["errors"]["local_repository_roots"] == "code_config_invalid_local_root"


def test_import_limits_and_retention_are_validated(tmp_path):
    result = _valid_settings(
        tmp_path,
        code_import_max_files=0,
        code_snapshot_retention_hours=0,
        code_workspace_retention_hours=-1,
    ).code_agent_security_readiness()
    assert result["ready"] is False
    assert result["errors"] == {
        "import_max_files": "code_config_limit_must_be_positive",
        "snapshot_retention_hours": "code_config_limit_must_be_positive",
        "workspace_retention_hours": "code_config_retention_must_be_non_negative",
    }


def test_storage_capacity_limits_must_be_positive(tmp_path):
    result = _valid_settings(
        tmp_path,
        code_snapshot_capacity_bytes=0,
        code_workspace_capacity_bytes=0,
        code_storage_low_watermark_bytes=0,
    ).code_agent_security_readiness()

    assert result["errors"] == {
        "snapshot_capacity_bytes": "code_config_limit_must_be_positive",
        "workspace_capacity_bytes": "code_config_limit_must_be_positive",
        "storage_low_watermark_bytes": "code_config_limit_must_be_positive",
    }
