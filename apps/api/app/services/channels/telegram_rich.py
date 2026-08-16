"""Telegram Rich Message helpers: keep standard Markdown for sendRichMessage.

Primary path: pass GitHub-flavored Markdown (tables included) to Bot API
``sendRichMessage`` via ``rich_message={"markdown": ...}``.

Wide tables (>6 columns) are split into several **standard Markdown tables**
(4–6 data columns + key column) by business field groups — never ASCII/pre/image.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from app.services.markdown_renderer import (
    extract_final_display_content,
    prepare_markdown_for_preview,
)

# Prefer these column groups when splitting ops / export tables.
_BUSINESS_COLUMN_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("注册与登录", ("注册", "登录", "新增", "累计", "活跃")),
    ("充值", ("充值", "付费", "付费率")),
    ("提现", ("提现", "充提", "出款")),
    ("投注与ROI", ("下注", "投注", "流水", "ROI", "roi", "sc")),
    ("用户结构", ("用户", "封禁", "渠道", "游戏", "设备", "系统")),
)

_TABLE_BLOCK_RE = re.compile(
    r"(?:^|\n)("
    r"\|[^\n]+\|\s*\n"
    r"\|[\s:|-]+\|\s*\n"
    r"(?:\|[^\n]+\|\s*\n?)+"
    r")",
    re.MULTILINE,
)


def _parse_row(line: str) -> list[str]:
    raw = line.strip()
    if not raw.startswith("|"):
        return []
    parts = raw.strip("|").split("|")
    return [p.strip() for p in parts]


def _is_sep_row(cells: list[str]) -> bool:
    if not cells:
        return False
    return all(re.fullmatch(r":?-{3,}:?", c.replace(" ", "")) for c in cells)


def _parse_table_block(block: str) -> tuple[list[str], list[str], list[list[str]]] | None:
    lines = [ln for ln in block.strip().splitlines() if ln.strip()]
    if len(lines) < 2:
        return None
    header = _parse_row(lines[0])
    sep = _parse_row(lines[1])
    if not header or not _is_sep_row(sep):
        return None
    rows: list[list[str]] = []
    for line in lines[2:]:
        cells = _parse_row(line)
        if not cells:
            continue
        while len(cells) < len(header):
            cells.append("")
        rows.append(cells[: len(header)])
    aligns = []
    for cell in sep:
        c = cell.replace(" ", "")
        if c.startswith(":") and c.endswith(":"):
            aligns.append(":---:")
        elif c.endswith(":"):
            aligns.append("---:")
        else:
            aligns.append("---")
    while len(aligns) < len(header):
        aligns.append("---")
    return header, aligns[: len(header)], rows


def _format_table(header: list[str], aligns: list[str], rows: list[list[str]]) -> str:
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(aligns) + " |",
    ]
    for row in rows:
        cells = list(row) + [""] * max(0, len(header) - len(row))
        lines.append("| " + " | ".join(cells[: len(header)]) + " |")
    return "\n".join(lines)


def _column_group_name(header: str) -> str:
    h = (header or "").lower()
    for name, keys in _BUSINESS_COLUMN_GROUPS:
        for key in keys:
            if key.lower() in h or key in header:
                return name
    return "其他指标"


def _pack_indices(indices: list[int], *, max_data_cols: int = 5) -> list[list[int]]:
    """Pack column indices into chunks of 1..max_data_cols (excluding key)."""
    if not indices:
        return []
    chunks: list[list[int]] = []
    current: list[int] = []
    for idx in indices:
        current.append(idx)
        if len(current) >= max_data_cols:
            chunks.append(current)
            current = []
    if current:
        # Prefer not leaving a tiny tail of 1 when previous can absorb within 6 data cols
        if chunks and len(current) < 2 and len(chunks[-1]) + len(current) <= max_data_cols:
            chunks[-1].extend(current)
        else:
            chunks.append(current)
    return chunks


def split_wide_table(
    header: list[str],
    aligns: list[str],
    rows: list[list[str]],
    *,
    max_cols: int = 6,
    min_cols: int = 4,
) -> list[tuple[str, str]]:
    """Split a wide table into titled standard Markdown tables.

    Returns list of (optional_heading, markdown_table).
    Key/date column (index 0) is repeated in each sub-table.
    """
    col_count = len(header)
    if col_count <= max_cols:
        return [("", _format_table(header, aligns, rows))]

    key_idx = 0
    # Group remaining columns by business keywords, preserve original order inside groups
    grouped: dict[str, list[int]] = {}
    order: list[str] = []
    for idx in range(1, col_count):
        name = _column_group_name(header[idx])
        if name not in grouped:
            grouped[name] = []
            order.append(name)
        grouped[name].append(idx)

    # Flatten groups into ordered column indices, then pack to 4–5 data cols (+ key = 5–6)
    max_data = max(1, max_cols - 1)
    # Prefer at least (min_cols - 1) data columns when possible
    packed: list[tuple[str, list[int]]] = []
    for name in order:
        idxs = grouped[name]
        for chunk in _pack_indices(idxs, max_data_cols=max_data):
            # If a single-group chunk is smaller than min_cols-1 and next exists, leave as-is
            # (don't merge across business groups — keeps field semantics)
            packed.append((name, chunk))

    # Merge tiny adjacent packs from same group already handled; merge tiny packs across
    # groups only when both are small and combined ≤ max_data
    merged: list[tuple[str, list[int]]] = []
    for name, chunk in packed:
        min_data = max(1, min_cols - 1)
        if (
            merged
            and len(merged[-1][1]) < min_data
            and len(chunk) < min_data
            and len(merged[-1][1]) + len(chunk) <= max_data
        ):
            prev_name, prev_cols = merged[-1]
            title = prev_name if prev_name == name else f"{prev_name} / {name}"
            merged[-1] = (title, prev_cols + chunk)
        else:
            merged.append((name, chunk))

    out: list[tuple[str, str]] = []
    for name, data_idxs in merged:
        idxs = [key_idx, *data_idxs]
        sub_header = [header[i] for i in idxs]
        sub_aligns = [aligns[i] if i < len(aligns) else "---" for i in idxs]
        sub_rows = [[row[i] if i < len(row) else "" for i in idxs] for row in rows]
        title = f"#### {name}" if name else ""
        out.append((title, _format_table(sub_header, sub_aligns, sub_rows)))
    return out or [("", _format_table(header, aligns, rows))]


def rewrite_wide_markdown_tables(text: str, *, max_cols: int = 6) -> str:
    """Replace GFM tables with >max_cols columns by several smaller Markdown tables."""
    source = str(text or "")
    if not source.strip():
        return source

    def repl(match: re.Match[str]) -> str:
        block = match.group(1)
        parsed = _parse_table_block(block)
        if not parsed:
            return match.group(0)
        header, aligns, rows = parsed
        if len(header) <= max_cols:
            return match.group(0)
        parts: list[str] = []
        for title, table_md in split_wide_table(header, aligns, rows, max_cols=max_cols):
            if title:
                parts.append(title)
            parts.append(table_md)
        prefix = "\n" if match.group(0).startswith("\n") else ""
        return prefix + "\n\n".join(parts) + ("\n" if match.group(0).endswith("\n") else "")

    return _TABLE_BLOCK_RE.sub(repl, source)


def prepare_telegram_rich_markdown(text: str) -> str:
    """Prepare Agent Markdown for sendRichMessage (still standard Markdown)."""
    source = extract_final_display_content(text)
    source = prepare_markdown_for_preview(source)
    source = rewrite_wide_markdown_tables(source, max_cols=6)
    return (source or "").strip() or "(空回复)"


def iter_rich_markdown_chunks(text: str, limit: int = 30000) -> Iterable[str]:
    """Split long Markdown into Rich Message sized chunks on blank lines."""
    source = prepare_telegram_rich_markdown(text)
    if len(source) <= limit:
        yield source
        return
    parts = re.split(r"\n{2,}", source)
    current = ""
    for part in parts:
        candidate = f"{current}\n\n{part}" if current else part
        if current and len(candidate) > limit:
            yield current
            current = part
        else:
            current = candidate
    if current:
        yield current
