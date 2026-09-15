from pathlib import Path

from app.config import get_settings
from app.services.site_config import (
    bundled_site_logo_path,
    ensure_default_site_logo,
    resolved_site_logo_file,
    _resolve_site_logo,
)


def test_bundled_site_logo_exists():
    assert bundled_site_logo_path().is_file()


def test_ensure_default_site_logo_copies_bundled(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    dest = ensure_default_site_logo()
    assert dest.is_file()
    assert dest.read_bytes() == bundled_site_logo_path().read_bytes()
    url = _resolve_site_logo("")
    assert url.startswith("/statics/site/logo.png?v=")
    get_settings.cache_clear()


def test_ensure_default_site_logo_keeps_existing_upload(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    dest = tmp_path / "uploads" / "site" / "logo.png"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"existing-logo")
    copied = ensure_default_site_logo()
    assert copied.read_bytes() == b"existing-logo"
    get_settings.cache_clear()


def test_resolved_site_logo_file_prefers_upload(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    dest = tmp_path / "uploads" / "site" / "logo.png"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"upload")
    assert resolved_site_logo_file() == dest.resolve()
    get_settings.cache_clear()
