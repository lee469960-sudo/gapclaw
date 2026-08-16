import hashlib
import secrets
import uuid
from datetime import datetime, timedelta
import bcrypt
import base64
from cryptography.fernet import Fernet

from app.config import get_settings


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


def new_id() -> str:
    return uuid.uuid4().hex[:8]


def new_token() -> str:
    return secrets.token_hex(16)


def _fernet() -> Fernet:
    key = hashlib.sha256(get_settings().secret_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_secret(value: str) -> str:
    if not value:
        return ""
    return _fernet().encrypt(value.encode()).decode()


def decrypt_secret(value: str) -> str:
    if not value:
        return ""
    try:
        return _fernet().decrypt(value.encode()).decode()
    except Exception:
        return value


def mask_secret(value: str, visible: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= visible * 2:
        return "*" * len(value)
    return value[:visible] + "*" * (len(value) - visible * 2) + value[-visible:]


def is_masked_secret(value: str) -> bool:
    """Detect secrets returned by mask_secret (must not be saved back)."""
    if not value:
        return False
    return "*" in value


def now_str() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def session_expiry() -> datetime:
    return datetime.utcnow() + timedelta(hours=get_settings().session_ttl_hours)
