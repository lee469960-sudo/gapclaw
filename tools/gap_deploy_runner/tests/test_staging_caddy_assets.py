from __future__ import annotations

from pathlib import Path


ASSETS = Path(__file__).parents[1] / "assets"


def test_staging_caddy_separates_browser_callback_and_private_control_paths():
    caddyfile = (ASSETS / "Caddyfile.staging").read_text(encoding="utf-8")

    assert "staging.gapclaw.online {" in caddyfile
    assert "runner-staging.gapclaw.online {" in caddyfile
    assert "reverse_proxy 127.0.0.1:18000" in caddyfile
    assert "reverse_proxy 127.0.0.1:18080" in caddyfile
    assert "mode require_and_verify" in caddyfile
    assert "trust_pool file /etc/caddy/staging/runner-ca.crt" in caddyfile
    assert "@release_callback path /internal/release-runner/callback" in caddyfile
    assert "header_up X-Gap-Runner-Client-Verify SUCCESS" in caddyfile
    assert 'respond "not found" 404' in caddyfile
    for forbidden in (
        "/v1/status", "/v1/health", "/v1/rollback", "gap-runner-staging.internal",
        "\ngapclaw.online {", "\nrunner.gapclaw.online {", "nginx", "gap-api", "gap-web",
    ):
        assert forbidden not in caddyfile


def test_staging_caddy_installer_validates_a_reviewed_import_before_reload():
    installer = (ASSETS / "install-staging-caddy.sh").read_text(encoding="utf-8")

    assert "command -v caddy" in installer
    assert "import /etc/caddy/sites/*.Caddyfile" in installer
    assert "caddy_staging_import_missing" in installer
    assert "caddy validate --config \"$main_file\" --adapter caddyfile" in installer
    assert "systemctl reload caddy" in installer
    assert "nginx" not in installer.lower()


def test_staging_compose_exposes_only_loopback_proxy_ports_and_no_nginx_networks():
    compose = (ASSETS / "gap-staging.compose.yml").read_text(encoding="utf-8")

    assert '"127.0.0.1:${API_PORT:-18000}:8000"' in compose
    assert '"127.0.0.1:${WEB_PORT:-18080}:8080"' in compose
    assert '"gap-runner-staging.internal:host-gateway"' in compose
    assert '"host.docker.internal:host-gateway"' in compose
    for forbidden in ("gap-staging-edge", "gap-staging-proxy", "nginx", '"80:80"', '"443:443"'):
        assert forbidden not in compose
