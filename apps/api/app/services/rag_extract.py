"""Extract plain text from uploaded RAG documents."""

from pathlib import Path


SUPPORTED_SUFFIXES = {".txt", ".md", ".csv", ".json", ".pdf", ".docx"}


class RagExtractError(Exception):
    pass


def extract_text(path: str | Path) -> str:
    p = Path(path)
    if not p.is_file():
        raise RagExtractError("文件不存在")
    suffix = p.suffix.lower()
    if suffix == ".doc":
        raise RagExtractError("不支持 .doc，请转换为 .docx / .pdf / .txt 后上传")
    if suffix not in SUPPORTED_SUFFIXES:
        raise RagExtractError(f"不支持的文件类型: {suffix or '(无扩展名)'}")
    if suffix in {".txt", ".md", ".csv", ".json"}:
        text = p.read_text(encoding="utf-8", errors="replace")
    elif suffix == ".pdf":
        text = _extract_pdf(p)
    else:
        text = _extract_docx(p)
    text = (text or "").strip()
    if not text:
        raise RagExtractError("未能从文件中提取到可用文本")
    return text


def _extract_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise RagExtractError("缺少 pypdf 依赖") from e
    reader = PdfReader(str(path))
    parts = []
    for page in reader.pages:
        parts.append(page.extract_text() or "")
    return "\n".join(parts)


def _extract_docx(path: Path) -> str:
    try:
        from docx import Document
    except ImportError as e:
        raise RagExtractError("缺少 python-docx 依赖") from e
    doc = Document(str(path))
    return "\n".join(p.text for p in doc.paragraphs if p.text)
