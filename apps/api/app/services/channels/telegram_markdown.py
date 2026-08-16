"""Telegram HTML fallback adapter (sendMessage) over shared Markdown Renderer.

Primary delivery uses ``sendRichMessage`` + standard Markdown
(``telegram_rich``). This module is only the **fallback** path when Rich
Message fails — it must not convert tables to ``<pre>`` / ASCII / images.
"""

from __future__ import annotations

from html import escape
from urllib.parse import urlparse

from bs4 import BeautifulSoup, NavigableString, Tag

from app.services.markdown_renderer import markdown_to_soup


def _children_html(node: Tag) -> str:
    return "".join(_inline_html(child) for child in node.children)


def _safe_link(value: str) -> str:
    href = (value or "").strip()
    parsed = urlparse(href)
    return href if parsed.scheme in ("http", "https", "tg") else ""


def _inline_html(node) -> str:
    if isinstance(node, NavigableString):
        return escape(str(node), quote=False)
    if not isinstance(node, Tag):
        return ""
    name = node.name.lower()
    inner = _children_html(node)
    if name in ("strong", "b"):
        return f"<b>{inner}</b>"
    if name in ("em", "i"):
        return f"<i>{inner}</i>"
    if name in ("del", "s", "strike"):
        return f"<s>{inner}</s>"
    if name == "u":
        return f"<u>{inner}</u>"
    if name == "code":
        return f"<code>{escape(node.get_text(), quote=False)}</code>"
    if name == "a":
        href = _safe_link(str(node.get("href") or ""))
        return f'<a href="{escape(href, quote=True)}">{inner}</a>' if href else inner
    if name == "br":
        return "\n"
    return inner


def _table_html_blocks(table: Tag) -> list[str]:
    """Fallback-only: compact mobile blocks (not <pre>, not images)."""
    rows = table.find_all("tr")
    if not rows:
        return []
    parsed_rows: list[list[str]] = []
    for row in rows:
        cells = row.find_all(("th", "td"), recursive=False)
        parsed_rows.append([_children_html(cell).strip() for cell in cells])
    parsed_rows = [row for row in parsed_rows if row]
    if not parsed_rows:
        return []

    first_cells = rows[0].find_all(("th", "td"), recursive=False)
    has_header = any(cell.name == "th" for cell in first_cells)
    headers = parsed_rows[0] if has_header else []
    data_rows = parsed_rows[1:] if has_header else parsed_rows
    blocks: list[str] = []
    for values in data_rows:
        if len(values) == 2:
            blocks.append(f"• <b>{values[0]}</b>：{values[1]}")
            continue
        title = values[0]
        detail_items: list[str] = []
        for index, value in enumerate(values[1:], start=1):
            label = headers[index] if index < len(headers) else f"第 {index + 1} 列"
            detail_items.append(f"<code>{label}</code> {value}")
        detail_lines = [
            "  ·  ".join(detail_items[index:index + 2])
            for index in range(0, len(detail_items), 2)
        ]
        body = "\n".join(detail_lines)
        blocks.append(f"<blockquote><b>{title}</b>\n{body}</blockquote>")
    return blocks


def _list_html(node: Tag, *, ordered: bool) -> str:
    lines: list[str] = []
    items = node.find_all("li", recursive=False)
    for index, item in enumerate(items, start=1):
        prefix = f"{index}." if ordered else "•"
        direct = "".join(
            _inline_html(child)
            for child in item.children
            if not (isinstance(child, Tag) and child.name in ("ul", "ol"))
        ).strip()
        if direct:
            lines.append(f"{prefix} {direct}")
        for nested in item.find_all(("ul", "ol"), recursive=False):
            nested_text = _list_html(nested, ordered=nested.name == "ol")
            if nested_text:
                lines.extend(f"  {line}" for line in nested_text.splitlines())
    return "\n".join(lines)


def _block_html(node) -> str:
    if isinstance(node, NavigableString):
        return escape(str(node).strip(), quote=False)
    if not isinstance(node, Tag):
        return ""
    name = node.name.lower()
    if name in ("h1", "h2", "h3", "h4", "h5", "h6"):
        return f"<b>{_children_html(node).strip()}</b>"
    if name == "p":
        lines = [line.strip() for line in _children_html(node).splitlines() if line.strip()]
        normalized = [f"• {line[2:]}" if line.startswith("- ") else line for line in lines]
        return "\n".join(normalized)
    if name == "pre":
        return f"<pre>{escape(node.get_text(), quote=False)}</pre>"
    if name == "blockquote":
        return f"<blockquote>{_children_html(node).strip()}</blockquote>"
    if name == "ul":
        return _list_html(node, ordered=False)
    if name == "ol":
        return _list_html(node, ordered=True)
    if name == "hr":
        return "────────"
    if name == "table":
        return "\n".join(_table_html_blocks(node))
    return _children_html(node).strip()


def markdown_to_telegram_blocks(text: str) -> list[str]:
    """Fallback: parse Markdown → Telegram HTML blocks (no table→pre)."""
    source = (text or "").strip()
    if not source:
        return ["(空回复)"]
    soup = markdown_to_soup(source)
    blocks: list[str] = []
    for node in soup.contents:
        if isinstance(node, Tag) and node.name.lower() == "table":
            blocks.extend(_table_html_blocks(node))
            continue
        block = _block_html(node).strip()
        if block:
            blocks.append(block)
    return blocks or ["(空回复)"]


def _split_large_block(block: str, limit: int) -> list[str]:
    soup = BeautifulSoup(block, "html.parser")
    only = next((node for node in soup.contents if isinstance(node, Tag)), None)
    is_pre = bool(only and only.name == "pre")
    plain = soup.get_text("\n").strip()
    wrapper_size = len("<pre></pre>") if is_pre else 0
    width = max(1, limit - wrapper_size)
    chunks = [plain[pos:pos + width] for pos in range(0, len(plain), width)] or [""]
    if is_pre:
        return [f"<pre>{escape(chunk, quote=False)}</pre>" for chunk in chunks]
    return [escape(chunk, quote=False) for chunk in chunks]


def telegram_html_chunks(text: str, limit: int = 3500) -> list[str]:
    """Fallback sendMessage HTML chunks when sendRichMessage fails."""
    blocks = markdown_to_telegram_blocks(text)
    expanded: list[str] = []
    for block in blocks:
        expanded.extend(_split_large_block(block, limit) if len(block) > limit else [block])

    chunks: list[str] = []
    current = ""
    for block in expanded:
        candidate = f"{current}\n\n{block}" if current else block
        if current and len(candidate) > limit:
            chunks.append(current)
            current = block
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks or ["(空回复)"]
