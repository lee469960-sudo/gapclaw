from pathlib import Path

from sqlalchemy.orm import Session

from app.models import SiteConfig

DEFAULT_SITE_NAME = "GAP — 智能工作台"


def _site_logo_path() -> Path:
    from app.config import get_settings
    return Path(get_settings().data_dir).resolve() / "uploads" / "site" / "logo.png"


def site_logo_path() -> Path:
    path = _site_logo_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _resolve_site_logo(raw: str) -> str:
    if not raw:
        return ""
    base = raw.split("?", 1)[0]
    if base not in ("/statics/site/logo.png", "/statics/uploads/site/logo.png"):
        return raw
    logo = _site_logo_path()
    if logo.is_file():
        return f"/statics/site/logo.png?v={int(logo.stat().st_mtime)}"
    return ""


def get_site_config(db: Session) -> dict:
    configs = {c.key: c.value for c in db.query(SiteConfig).all()}
    return {
        "site_name": configs.get("site_name") or DEFAULT_SITE_NAME,
        "site_logo": _resolve_site_logo(configs.get("site_logo") or ""),
        "footer": configs.get("footer") or "",
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
