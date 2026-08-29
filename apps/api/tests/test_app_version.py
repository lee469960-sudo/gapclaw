from pathlib import Path

from app.version import app_version, format_footer


def test_app_version_reads_file(tmp_path, monkeypatch):
    path = tmp_path / "gap.version"
    path.write_text("V9.9.9\n", encoding="utf-8")
    monkeypatch.delenv("GAP_VERSION", raising=False)
    monkeypatch.setenv("GAP_VERSION_FILE", str(path))
    app_version.cache_clear()
    assert app_version() == "V9.9.9"
    app_version.cache_clear()


def test_app_version_env_overrides_file(tmp_path, monkeypatch):
    path = tmp_path / "gap.version"
    path.write_text("V0.0.1\n", encoding="utf-8")
    monkeypatch.setenv("GAP_VERSION_FILE", str(path))
    monkeypatch.setenv("GAP_VERSION", "v2.0.0")
    app_version.cache_clear()
    assert app_version() == "v2.0.0"
    app_version.cache_clear()


def test_format_footer_empty_uses_version():
    assert format_footer("", "V0.0.3") == "V0.0.3"
    assert format_footer("   ", "v1.0.0") == "v1.0.0"


def test_format_footer_replaces_stale_version_only_text():
    assert format_footer("V0.0.1", "V0.0.3") == "V0.0.3"
    assert format_footer("v1.0.0", "v1.2.0") == "v1.2.0"


def test_format_footer_appends_version_once():
    assert format_footer("© GAP", "V0.0.3") == "© GAP · V0.0.3"
    assert format_footer("© GAP · V0.0.3", "V0.0.3") == "© GAP · V0.0.3"


def test_repo_gap_version_file_exists():
    repo = Path(__file__).resolve().parents[3] / "deploy" / "gap.version"
    assert repo.is_file()
    assert repo.read_text(encoding="utf-8").strip()
