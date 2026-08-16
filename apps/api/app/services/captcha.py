import json
import random
import secrets
import time
from pathlib import Path
from threading import Lock

from app.config import get_settings

_lock = Lock()
TTL_SECONDS = 300
_STORE_NAME = "captcha_store.json"


def _store_path() -> Path:
    root = Path(get_settings().data_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root / _STORE_NAME


def _load(now: float) -> dict[str, tuple[str, float]]:
    path = _store_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[str, tuple[str, float]] = {}
    if not isinstance(raw, dict):
        return out
    for cid, entry in raw.items():
        if not isinstance(cid, str) or not isinstance(entry, (list, tuple)) or len(entry) != 2:
            continue
        code, exp = entry
        try:
            exp_f = float(exp)
        except (TypeError, ValueError):
            continue
        if exp_f <= now:
            continue
        out[cid] = (str(code), exp_f)
    return out


def _save(store: dict[str, tuple[str, float]]) -> None:
    path = _store_path()
    payload = {cid: [code, exp] for cid, (code, exp) in store.items()}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def create_captcha() -> tuple[str, str]:
    captcha_id = secrets.token_hex(16)
    code = f"{random.randint(0, 9999):04d}"
    now = time.time()
    with _lock:
        store = _load(now)
        store[captcha_id] = (code, now + TTL_SECONDS)
        _save(store)
    return captcha_id, code


def verify_captcha(captcha_id: str | None, code: str | None) -> bool:
    if not captcha_id or not code:
        return False
    normalized = str(code).strip()
    if not normalized.isdigit() or len(normalized) != 4:
        return False
    now = time.time()
    with _lock:
        store = _load(now)
        entry = store.get(captcha_id)
        if not entry:
            return False
        expected, expires = entry
        if now > expires:
            store.pop(captcha_id, None)
            _save(store)
            return False
        if normalized != expected:
            # Wrong code: keep entry so user can retry
            return False
        store.pop(captcha_id, None)
        _save(store)
        return True
