"""Shared Markdown Renderer for channel / preview adapters.

Parses **standard Markdown** (same family as Web `marked` + GFM tables /
fenced code / lists). Downstream adapters map the HTML AST to a surface:

- Web: `apps/web` → ``marked`` + ``markdownPreview.js`` → browser HTML/CSS
- Telegram: ``telegram_rich`` → ``sendRichMessage`` (Markdown);
  HTML fallback via ``telegram_markdown.telegram_html_chunks``

Agent Core must keep emitting standard Markdown; this module never converts
to Telegram MarkdownV2.
"""

from __future__ import annotations

import json
import re

import markdown
from bs4 import BeautifulSoup

# Keep in sync with Web marked options: gfm tables, fenced code, lists, soft breaks.
_MARKDOWN_EXTENSIONS = [
    "tables",
    "fenced_code",
    "sane_lists",
    "nl2br",
]

_JSON_FENCE_RE = re.compile(
    r"```(?:json|JSON)?\s*\n([\s\S]*?)```",
    re.MULTILINE,
)


def extract_final_display_content(text: str) -> str:
    """Mirror Web ``extractFinalDisplayContent`` — keep text after last FINAL:."""
    source = str(text or "")
    marker = re.compile(r"^\s*FINAL\s*[:：]\s*", re.IGNORECASE | re.MULTILINE)
    last = None
    for match in marker.finditer(source):
        last = match
    if not last:
        return source
    return source[last.end() :].strip()


def _rows_to_markdown_table(rows: list[dict], max_rows: int = 30) -> str:
    if not rows:
        return ""
    cols = list(rows[0].keys())[:10]
    if not cols:
        return ""

    def cell_s(val) -> str:
        return str(val if val is not None else "").replace("|", "\\|").replace("\n", " ").strip()

    header = "| " + " | ".join(cols) + " |"
    sep = "| " + " | ".join("---" for _ in cols) + " |"
    body = [
        "| " + " | ".join(cell_s(row.get(c)) for c in cols) + " |"
        for row in rows[:max_rows]
    ]
    md = "\n".join([header, sep, *body])
    if len(rows) > max_rows:
        md += f"\n\n*共 {len(rows)} 条，展示前 {max_rows} 条*"
    return md


def prepare_markdown_for_preview(text: str) -> str:
    """Mirror Web ``prepareMarkdownForPreview`` — JSON row fences → GFM tables."""
    source = str(text or "")
    if not source.strip():
        return source

    def repl(match: re.Match[str]) -> str:
        body = (match.group(1) or "").strip()
        try:
            data = json.loads(body)
        except Exception:
            return match.group(0)
        rows = None
        if isinstance(data, list) and data and isinstance(data[0], dict):
            rows = data
        elif isinstance(data, dict):
            for key in ("data", "items", "records", "result", "rows"):
                val = data.get(key)
                if isinstance(val, list) and val and isinstance(val[0], dict):
                    rows = val
                    break
        if not rows:
            return match.group(0)
        table = _rows_to_markdown_table(rows, 30)
        if not table:
            return match.group(0)
        return f"### 查询结果\n\n{table}"

    return _JSON_FENCE_RE.sub(repl, source)


def markdown_to_html(text: str, *, prepare: bool = True) -> str:
    """Parse standard Markdown to HTML (shared parse step for adapters)."""
    source = extract_final_display_content(text)
    if prepare:
        source = prepare_markdown_for_preview(source)
    source = (source or "").strip()
    if not source:
        return ""
    return markdown.markdown(
        source,
        extensions=list(_MARKDOWN_EXTENSIONS),
        output_format="html",
    )


def markdown_to_soup(text: str, *, prepare: bool = True) -> BeautifulSoup:
    """Parse Markdown and return a BeautifulSoup document."""
    return BeautifulSoup(markdown_to_html(text, prepare=prepare), "html.parser")
