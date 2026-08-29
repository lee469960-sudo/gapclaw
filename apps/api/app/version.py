"""Product version from deploy/gap.version (env override GAP_VERSION)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import os
import re

_VERSION_ONLY = re.compile(r"^[vV]?\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")


def _version_candidates() -> list[Path]:
    here = Path(__file__).resolve()
    paths: list[Path] = []
    env_file = (os.environ.get("GAP_VERSION_FILE") or "").strip()
    if env_file:
        paths.append(Path(env_file))
    paths.extend(
        [
            Path("/app/deploy/gap.version"),
            here.parents[3] / "deploy" / "gap.version",  # repo: apps/api/app/version.py
            here.parents[1] / "deploy" / "gap.version",  # image: /app/app/version.py
        ]
    )
    return paths


@lru_cache(maxsize=1)
def app_version() -> str:
    env = (os.environ.get("GAP_VERSION") or "").strip()
    if env:
        return env
    for path in _version_candidates():
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            return text
    return "unknown"


def format_footer(custom: str | None, version: str | None = None) -> str:
    ver = (version if version is not None else app_version()).strip()
    text = (custom or "").strip()
    if not text or _VERSION_ONLY.match(text):
        return ver
    if ver and ver in text:
        return text
    if not ver:
        return text
    return f"{text} · {ver}"
