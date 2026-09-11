from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.deps import get_session_user
from app.models import User
from app.routers import release_management
from app.services.release_config import ReleaseConfigError, ReleaseManagementConfig


def _settings(**overrides) -> Settings:
    values = {
        "release_environment": "production",
        "release_runner_ca_file": "/run/secrets/release-ca.pem",
        "release_runner_client_cert_file": "/run/secrets/release-client.pem",
        "release_runner_client_key_file": "/run/secrets/release-client-key.pem",
        "release_runner_timeout_seconds": 12,
    }
    values.update(overrides)
    return Settings(**values)


def test_release_config_builds_fixed_runner_tls_and_hides_certificate_references():
    config = ReleaseManagementConfig.from_settings(_settings())

    assert config.runner_tls().base_url == "https://gap-runner.internal:9443"
    public = config.public_view()
    assert public["ready"] is True
    assert public["client_identity_configured"] is True
    rendered = json.dumps(public)
    assert "/run/secrets" not in rendered
    assert "release-client-key.pem" not in rendered
    assert "ACR_PASSWORD" not in rendered


def test_staging_bundle_is_process_fixed_and_redacts_runner_tls_details():
    config = ReleaseManagementConfig.from_settings(_settings(
        release_environment="staging",
        release_target_id="production",
        release_runner_base_url="https://gap-runner.internal:9443",
    ))

    assert config.environment == config.target_id == "staging"
    assert config.runner_tls().base_url == "https://gap-runner-staging.internal:9443"
    assert config.callback_identity == "runner-staging.gapclaw.online"
    public = config.public_view()
    assert public["environment"] == "staging"
    assert "runner_base_url" not in public
    assert "gap-runner-staging.internal" not in json.dumps(public)


def test_release_config_rejects_unknown_process_environment():
    with pytest.raises(ReleaseConfigError, match="release_environment_invalid"):
        ReleaseManagementConfig.from_settings(_settings(release_environment="preview"))


def test_release_config_fails_closed_for_arbitrary_runner_or_invalid_tls_reference():
    with pytest.raises(ReleaseConfigError, match="release_mtls_reference_invalid"):
        ReleaseManagementConfig.from_settings(_settings(release_runner_client_key_file="relative.pem"))


def test_release_config_api_returns_only_redacted_view(monkeypatch):
    app = FastAPI()
    app.include_router(release_management.router)
    app.dependency_overrides[get_session_user] = lambda: User(username="viewer", password_hash="x")
    monkeypatch.setattr(release_management, "get_settings", lambda: _settings())

    response = TestClient(app).get("/api/release-management/config")

    assert response.status_code == 200
    rendered = response.text
    assert '"ready":true' in rendered
    assert "/run/secrets" not in rendered
    assert "release-client-key.pem" not in rendered


def test_release_config_api_reports_missing_host_references_without_exposing_them(monkeypatch):
    app = FastAPI()
    app.include_router(release_management.router)
    app.dependency_overrides[get_session_user] = lambda: User(username="viewer", password_hash="x")
    monkeypatch.setattr(release_management, "get_settings", lambda: Settings())

    response = TestClient(app).get("/api/release-management/config")

    assert response.status_code == 200
    assert response.json()["data"]["ready"] is False
    assert response.json()["data"]["reason"] == "release_mtls_reference_invalid"
