from pathlib import Path
import shutil

from sqlalchemy.orm import Session

from app.models import SiteConfig
from app.version import app_version, format_footer

DEFAULT_SITE_NAME = "GAP — 智能工作台"
DEFAULT_SITE_LOGO_URL = "/statics/site/logo.png"


def bundled_site_logo_path() -> Path:
    return Path(__file__).resolve().parents[2] / "static" / "site" / "logo.png"


def _site_logo_path() -> Path:
    from app.config import get_settings
    return Path(get_settings().data_dir).resolve() / "uploads" / "site" / "logo.png"


def site_logo_path() -> Path:
    path = _site_logo_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def ensure_default_site_logo() -> Path:
    dest = site_logo_path()
    if dest.is_file():
        return dest
    bundled = bundled_site_logo_path()
    if bundled.is_file():
        shutil.copyfile(bundled, dest)
    return dest


def resolved_site_logo_file() -> Path | None:
    ensure_default_site_logo()
    dest = _site_logo_path()
    if dest.is_file():
        return dest
    bundled = bundled_site_logo_path()
    if bundled.is_file():
        return bundled
    return None


def _logo_url_for(path: Path) -> str:
    return f"{DEFAULT_SITE_LOGO_URL}?v={int(path.stat().st_mtime)}"


def _resolve_site_logo(raw: str) -> str:
    logo = resolved_site_logo_file()
    if not raw:
        return _logo_url_for(logo) if logo else ""
    base = raw.split("?", 1)[0]
    if base not in (DEFAULT_SITE_LOGO_URL, "/statics/uploads/site/logo.png"):
        return raw
    if logo:
        return _logo_url_for(logo)
    return ""


def get_site_config(db: Session) -> dict:
    configs = {c.key: c.value for c in db.query(SiteConfig).all()}
    version = configs.get("version") or app_version()
    footer = configs.get("footer") or ""
    return {
        "site_name": configs.get("site_name") or DEFAULT_SITE_NAME,
        "site_logo": _resolve_site_logo(configs.get("site_logo") or ""),
        "footer": footer,
        "footer_display": format_footer(footer, version),
        "version": version,
        "feature_flags": configs.get("feature_flags") or "{}",
    }


def save_site_config(db: Session, values: dict) -> None:
    for key, value in values.items():
        cfg = db.query(SiteConfig).filter(SiteConfig.key == key).first()
        if not cfg:
            cfg = SiteConfig(key=key, value=value or "")
            db.add(cfg)
        else:
            cfg.value = value or ""
    db.commit()
