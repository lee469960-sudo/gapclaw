"""IM completion reply: pick the newest valid channel attachment.

Extracted from the deleted react_engine so the channel runtime no longer imports
the monolithic engine. Only file-path resolution lives here; actual push is done
by the platform adapter.
"""

from __future__ import annotations

import re

from app.services.workplace import download_path, is_valid_deliverable_file

_DATA_FILE_SUFFIXES = (".xlsx", ".xls", ".csv")

# react-engine-v7 R4: explicit delivery-path annotation (`attach=path1,path2` inline,
# or a standalone `ATTACH: path1,path2` line). Parse is channel-agnostic; sending is TG-only.
_ATTACH_LINE_RE = re.compile(r"(?im)^[ \t]*ATTACH[ \t]*:[ \t]*([^\n]*)\r?\n?")
_ATTACH_INLINE_RE = re.compile(
    r"attach\s*=\s*([^\s,]+(?:\s*,\s*[^\s,]+)*)", re.IGNORECASE
)


def _clean_attach_token(token: str) -> str:
    return (token or "").strip().strip("`").strip()


def parse_attach_paths(reply: str) -> list[str]:
    """Extract explicit delivery paths from an `attach=` / `ATTACH:` annotation."""
    text = reply or ""
    paths: list[str] = []
    for m in _ATTACH_LINE_RE.finditer(text):
        for part in m.group(1).split(","):
            p = _clean_attach_token(part)
            if p:
                paths.append(p)
    for m in _ATTACH_INLINE_RE.finditer(text):
        for part in m.group(1).split(","):
            p = _clean_attach_token(part)
            if p:
                paths.append(p)
    # Dedupe preserving order.
    seen: set[str] = set()
    out: list[str] = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def strip_attach_markers(reply: str) -> str:
    """Remove attach annotations from the visible reply text."""
    text = reply or ""
    text = _ATTACH_LINE_RE.sub("", text)
    text = _ATTACH_INLINE_RE.sub("", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def resolve_attach_paths(paths: list[str], sandbox_id: str) -> list[str]:
    """Keep only attach paths that resolve inside the sandbox (防穿越 + existence)."""
    if not sandbox_id:
        return []
    out: list[str] = []
    for p in paths or []:
        rel = (p or "").strip().lstrip("/")
        if rel and download_path(sandbox_id, rel):
            out.append(rel)
    return out


def _is_data_export_path(path: str) -> bool:
    lower = (path or "").lower()
    return any(lower.endswith(suf) for suf in _DATA_FILE_SUFFIXES)


def _rel_mtime(sandbox_id: str, rel: str) -> float:
    p = download_path(sandbox_id, rel) if sandbox_id else None
    if not p or not p.exists():
        return 0.0
    try:
        return float(p.stat().st_mtime)
    except OSError:
        return 0.0


def _pick_newest_rel(sandbox_id: str, rels: list[str]) -> str | None:
    clean = [r for r in (rels or []) if (r or "").strip()]
    if not clean:
        return None
    if not sandbox_id:
        return clean[0]
    return max(clean, key=lambda r: _rel_mtime(sandbox_id, r))


def _is_root_deliverable_rel(rel: str) -> bool:
    rel = (rel or "").strip().lstrip("/")
    if not rel or not _is_data_export_path(rel):
        return False
    if rel.startswith("task/") or "_engine_checkpoints" in rel:
        return False
    return True


def format_im_completion_reply(
    reply: str,
    *,
    saved_paths: list[str] | None = None,
    sandbox_id: str = "",
) -> tuple[str, list[str]]:
    """Return the Agent's actual reply plus its newest valid channel attachment."""
    text = (reply or "").strip()
    paths = [p for p in (saved_paths or []) if _is_root_deliverable_rel(p)]
    if sandbox_id and paths:
        kept: list[str] = []
        for p in paths:
            fp = download_path(sandbox_id, p)
            if fp and is_valid_deliverable_file(fp):
                kept.append(p)
        paths = kept
    one = _pick_newest_rel(sandbox_id, paths) if paths else None
    return text, [one] if one else []
