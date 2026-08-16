"""Render table-heavy Markdown replies as mobile-friendly Telegram images."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

import markdown
from bs4 import BeautifulSoup, NavigableString, Tag
from PIL import Image, ImageDraw, ImageFont


_REGULAR_FONT_CANDIDATES = (
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)
_BOLD_FONT_CANDIDATES = (
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)

_BACKGROUND = "#151827"
_PANEL = "#20253A"
_PANEL_ALT = "#262C45"
_HEADER = "#302A46"
_GRID = "#3D527C"
_TEXT = "#F5F7FF"
_MUTED = "#B9C0D4"
_ACCENT = "#43B5FF"
_QUOTE = "#1C3B35"
_QUOTE_LINE = "#45C72C"


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = _BOLD_FONT_CANDIDATES if bold else _REGULAR_FONT_CANDIDATES
    for candidate in candidates:
        if Path(candidate).is_file():
            try:
                return ImageFont.truetype(candidate, size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def _markdown_soup(text: str) -> BeautifulSoup:
    rendered = markdown.markdown(
        (text or "").strip(),
        extensions=["tables", "fenced_code", "sane_lists", "nl2br"],
        output_format="html",
    )
    return BeautifulSoup(rendered, "html.parser")


def has_markdown_table(text: str) -> bool:
    """Return whether Markdown contains at least one parsed table."""
    return _markdown_soup(text).find("table") is not None


def _text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> int:
    box = draw.textbbox((0, 0), text or " ", font=font)
    return max(0, box[2] - box[0])


def _wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_width: int,
) -> list[str]:
    value = " ".join(str(text or "").split())
    if not value:
        return [""]
    lines: list[str] = []
    current = ""
    for char in value:
        candidate = current + char
        if current and _text_width(draw, candidate, font) > max_width:
            lines.append(current.rstrip())
            current = char.lstrip()
        else:
            current = candidate
    if current or not lines:
        lines.append(current.rstrip())
    return lines


def _list_lines(node: Tag, depth: int = 0) -> list[str]:
    lines: list[str] = []
    ordered = node.name == "ol"
    for index, item in enumerate(node.find_all("li", recursive=False), start=1):
        prefix = f"{index}." if ordered else "•"
        direct = "".join(
            child.get_text(" ", strip=True) if isinstance(child, Tag) else str(child)
            for child in item.children
            if not (isinstance(child, Tag) and child.name in ("ul", "ol"))
        ).strip()
        if direct:
            lines.append(f"{'  ' * depth}{prefix} {direct}")
        for nested in item.find_all(("ul", "ol"), recursive=False):
            lines.extend(_list_lines(nested, depth + 1))
    return lines


def _parse_table(table: Tag) -> tuple[list[str], list[list[str]]]:
    parsed: list[list[str]] = []
    for row in table.find_all("tr"):
        cells = row.find_all(("th", "td"), recursive=False)
        values = [cell.get_text(" ", strip=True) for cell in cells]
        if values:
            parsed.append(values)
    if not parsed:
        return [], []
    first_cells = table.find("tr").find_all(("th", "td"), recursive=False)
    has_header = any(cell.name == "th" for cell in first_cells)
    if has_header:
        return parsed[0], parsed[1:]
    width = max(len(row) for row in parsed)
    return [f"第 {index + 1} 列" for index in range(width)], parsed


def _table_groups(
    headers: list[str],
    rows: list[list[str]],
    *,
    max_columns: int = 4,
    max_rows: int = 22,
) -> list[dict[str, Any]]:
    if not headers:
        return []
    width = len(headers)
    normalized = [row + [""] * max(0, width - len(row)) for row in rows]
    normalized = [row[:width] for row in normalized]
    if width <= max_columns:
        column_groups = [list(range(width))]
    else:
        data_width = max(1, max_columns - 1)
        column_groups = [
            [0, *range(start, min(width, start + data_width))]
            for start in range(1, width, data_width)
        ]
    groups: list[dict[str, Any]] = []
    row_pages = [normalized[pos:pos + max_rows] for pos in range(0, len(normalized), max_rows)] or [[]]
    for column_indexes in column_groups:
        for page_index, row_page in enumerate(row_pages):
            groups.append({
                "kind": "table",
                "headers": [headers[index] for index in column_indexes],
                "rows": [[row[index] for index in column_indexes] for row in row_page],
                "continued": page_index > 0,
            })
    return groups


def _document_blocks(text: str) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for node in _markdown_soup(text).contents:
        if isinstance(node, NavigableString):
            value = str(node).strip()
            if value:
                blocks.append({"kind": "paragraph", "text": value})
            continue
        if not isinstance(node, Tag):
            continue
        name = node.name.lower()
        if name == "table":
            headers, rows = _parse_table(node)
            blocks.extend(_table_groups(headers, rows))
        elif name in ("h1", "h2", "h3", "h4", "h5", "h6"):
            blocks.append({"kind": "heading", "level": int(name[1]), "text": node.get_text(" ", strip=True)})
        elif name in ("ul", "ol"):
            blocks.append({"kind": "list", "lines": _list_lines(node)})
        elif name == "blockquote":
            blocks.append({"kind": "quote", "text": node.get_text(" ", strip=True)})
        elif name == "pre":
            blocks.append({"kind": "code", "text": node.get_text("\n", strip=False).strip()})
        elif name == "hr":
            blocks.append({"kind": "rule"})
        else:
            value = node.get_text(" ", strip=True)
            if value:
                blocks.append({"kind": "paragraph", "text": value})
    return blocks


def _column_widths(total_width: int, count: int) -> list[int]:
    if count <= 0:
        return []
    base = total_width // count
    widths = [base] * count
    widths[-1] += total_width - sum(widths)
    return widths


def _measure_table(
    draw: ImageDraw.ImageDraw,
    block: dict[str, Any],
    content_width: int,
    table_font: ImageFont.ImageFont,
    table_bold: ImageFont.ImageFont,
) -> tuple[int, list[int], list[list[list[str]]]]:
    headers = block["headers"]
    rows = [headers, *block["rows"]]
    widths = _column_widths(content_width, len(headers))
    line_height = 38
    wrapped_rows: list[list[list[str]]] = []
    height = 0
    for row_index, row in enumerate(rows):
        font = table_bold if row_index == 0 else table_font
        wrapped = [
            _wrap_text(draw, value, font, max(20, widths[index] - 28))
            for index, value in enumerate(row)
        ]
        wrapped_rows.append(wrapped)
        height += max(66, max((len(lines) for lines in wrapped), default=1) * line_height + 26)
    return height + 24, widths, wrapped_rows


def _measure_block(
    draw: ImageDraw.ImageDraw,
    block: dict[str, Any],
    content_width: int,
    fonts: dict[str, ImageFont.ImageFont],
) -> int:
    kind = block["kind"]
    if kind == "table":
        height, _, _ = _measure_table(draw, block, content_width, fonts["table"], fonts["table_bold"])
        return height
    if kind == "rule":
        return 44
    if kind == "heading":
        level = min(3, int(block.get("level") or 3))
        font = fonts[f"h{level}"]
        return len(_wrap_text(draw, block["text"], font, content_width)) * (58 if level == 1 else 50) + 30
    if kind == "code":
        lines = []
        for raw_line in block["text"].splitlines() or [""]:
            lines.extend(_wrap_text(draw, raw_line, fonts["code"], content_width - 40))
        block["wrapped_lines"] = lines
        return max(78, len(lines) * 38 + 40) + 22
    if kind == "list":
        lines: list[str] = []
        for value in block["lines"]:
            lines.extend(_wrap_text(draw, value, fonts["body"], content_width))
        block["wrapped_lines"] = lines
        return max(1, len(lines)) * 46 + 24
    font = fonts["body"]
    available = content_width - 44 if kind == "quote" else content_width
    lines = _wrap_text(draw, block["text"], font, available)
    block["wrapped_lines"] = lines
    return max(1, len(lines)) * 46 + (42 if kind == "quote" else 24)


def _draw_lines(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    xy: tuple[int, int],
    *,
    font: ImageFont.ImageFont,
    fill: str,
    line_height: int,
) -> None:
    x, y = xy
    for line in lines:
        draw.text((x, y), line, font=font, fill=fill)
        y += line_height


def _draw_table(
    draw: ImageDraw.ImageDraw,
    block: dict[str, Any],
    x: int,
    y: int,
    content_width: int,
    fonts: dict[str, ImageFont.ImageFont],
) -> int:
    height, widths, wrapped_rows = _measure_table(
        draw, block, content_width, fonts["table"], fonts["table_bold"],
    )
    rows = [block["headers"], *block["rows"]]
    line_height = 38
    cursor_y = y
    for row_index, (row, wrapped) in enumerate(zip(rows, wrapped_rows)):
        row_height = max(66, max((len(lines) for lines in wrapped), default=1) * line_height + 26)
        fill = _HEADER if row_index == 0 else (_PANEL_ALT if row_index % 2 == 0 else _PANEL)
        cursor_x = x
        font = fonts["table_bold"] if row_index == 0 else fonts["table"]
        for column_index, _ in enumerate(row):
            width = widths[column_index]
            draw.rectangle(
                (cursor_x, cursor_y, cursor_x + width, cursor_y + row_height),
                fill=fill,
                outline=_GRID,
                width=2,
            )
            _draw_lines(
                draw,
                wrapped[column_index],
                (cursor_x + 14, cursor_y + 13),
                font=font,
                fill=_TEXT,
                line_height=line_height,
            )
            cursor_x += width
        cursor_y += row_height
    return height


def _draw_block(
    draw: ImageDraw.ImageDraw,
    block: dict[str, Any],
    x: int,
    y: int,
    content_width: int,
    fonts: dict[str, ImageFont.ImageFont],
) -> int:
    kind = block["kind"]
    height = _measure_block(draw, block, content_width, fonts)
    if kind == "table":
        return _draw_table(draw, block, x, y, content_width, fonts)
    if kind == "rule":
        draw.line((x + 170, y + 18, x + content_width - 170, y + 18), fill=_GRID, width=2)
        return height
    if kind == "heading":
        level = min(3, int(block.get("level") or 3))
        font = fonts[f"h{level}"]
        lines = _wrap_text(draw, block["text"], font, content_width)
        _draw_lines(draw, lines, (x, y), font=font, fill=_TEXT if level == 1 else _ACCENT, line_height=58 if level == 1 else 50)
        return height
    if kind == "quote":
        draw.rounded_rectangle((x, y, x + content_width, y + height - 18), radius=12, fill=_QUOTE)
        draw.rounded_rectangle((x, y, x + 8, y + height - 18), radius=4, fill=_QUOTE_LINE)
        _draw_lines(draw, block["wrapped_lines"], (x + 28, y + 18), font=fonts["body"], fill=_TEXT, line_height=46)
        return height
    if kind == "code":
        draw.rounded_rectangle((x, y, x + content_width, y + height - 18), radius=10, fill="#111522")
        _draw_lines(draw, block["wrapped_lines"], (x + 20, y + 18), font=fonts["code"], fill=_MUTED, line_height=38)
        return height
    lines = block.get("wrapped_lines") or []
    _draw_lines(draw, lines, (x, y), font=fonts["body"], fill=_TEXT, line_height=46)
    return height


def render_markdown_report_images(
    text: str,
    *,
    width: int = 1080,
    max_height: int = 7600,
) -> list[bytes]:
    """Render a Markdown report into one or more Telegram-safe PNG images."""
    blocks = _document_blocks(text)
    if not any(block["kind"] == "table" for block in blocks):
        return []

    fonts: dict[str, ImageFont.ImageFont] = {
        "h1": _font(44, bold=True),
        "h2": _font(39, bold=True),
        "h3": _font(35, bold=True),
        "body": _font(31),
        "table": _font(28),
        "table_bold": _font(29, bold=True),
        "code": _font(27),
    }
    margin = 48
    content_width = width - margin * 2
    probe = Image.new("RGB", (width, 200), _BACKGROUND)
    probe_draw = ImageDraw.Draw(probe)
    measured = [(_measure_block(probe_draw, block, content_width, fonts), block) for block in blocks]

    pages: list[list[tuple[int, dict[str, Any]]]] = []
    current: list[tuple[int, dict[str, Any]]] = []
    used = margin
    for height, block in measured:
        if current and used + height + margin > max_height:
            pages.append(current)
            current = []
            used = margin
        current.append((height, block))
        used += height
    if current:
        pages.append(current)

    images: list[bytes] = []
    for page in pages:
        page_height = min(max_height, max(360, margin * 2 + sum(height for height, _ in page)))
        image = Image.new("RGB", (width, page_height), _BACKGROUND)
        draw = ImageDraw.Draw(image)
        y = margin
        for _, block in page:
            y += _draw_block(draw, block, margin, y, content_width, fonts)
        output = BytesIO()
        image.save(output, format="PNG", optimize=True)
        images.append(output.getvalue())
    return images
