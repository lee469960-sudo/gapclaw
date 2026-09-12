from __future__ import annotations

from pathlib import Path


ASSETS = Path(__file__).parents[1] / "assets"


def test_production_caddy_separates_browser_callback_and_private_control_paths():
    caddyfile = (ASSETS / "Caddyfile.production").read_text(encoding="utf-8")

    assert "gapclaw.online {" in caddyfile
    assert "runner.gapclaw.online {" in caddyfile
    assert "reverse_proxy 127.0.0.1:8000" in caddyfile
    assert "reverse_proxy 127.0.0.1:8080" in caddyfile
    assert "mode require_and_verify" in caddyfile
    assert "tls /etc/caddy/production/runner-server.crt /etc/caddy/production/runner-server.key" in caddyfile
    assert "trust_pool file /etc/caddy/production/runner-ca.crt" in caddyfile
    assert "@release_hook {" in caddyfile
    assert "method POST" in caddyfile
    assert "path /internal/release-hook" in caddyfile
    assert "@release_hook_other_method path /internal/release-hook" in caddyfile
    assert "@release_callback path /internal/release-runner/callback" in caddyfile
    assert "header_up X-Gap-Runner-Client-Verify SUCCESS" in caddyfile
    assert 'respond "not found" 404' in caddyfile
    for forbidden in (
        "/v1/status", "/v1/health", "/v1/rollback", "gap-runner.internal",
        "staging.gapclaw.online", "runner-staging.gapclaw.online", "nginx", "gap-api", "gap-web",
    ):
        assert forbidden not in caddyfile


def test_production_caddy_installer_validates_a_reviewed_import_before_reload():
    installer = (ASSETS / "install-production-caddy.sh").read_text(encoding="utf-8")

    assert "command -v caddy" in installer
    assert "import /etc/caddy/sites/*.Caddyfile" in installer
    assert "caddy_production_import_missing" in installer
    assert "caddy_production_callback_cert_missing" in installer
    assert "caddy_production_callback_key_missing" in installer
    assert "caddy validate --config \"$main_file\" --adapter caddyfile" in installer
    assert "systemctl reload caddy" in installer
    assert "nginx" not in installer.lower()


def test_production_compose_exposes_only_loopback_proxy_ports_and_no_nginx_networks():
    compose = (ASSETS / "gap-prod.compose.yml").read_text(encoding="utf-8")

    assert '"127.0.0.1:${API_PORT:-8000}:8000"' in compose
    assert '"127.0.0.1:${WEB_PORT:-8080}:8080"' in compose
    assert '"gap-runner.internal:host-gateway"' in compose
    for forbidden in ("gap-edge", "gap-proxy", "nginx", '"80:80"', '"443:443"'):
        assert forbidden not in compose
