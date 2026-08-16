"""OpenAI-compatible embeddings client for RAG."""

from __future__ import annotations

import logging

import httpx

from app.config import get_settings
from app.services.llm_client import normalize_openai_base_url

logger = logging.getLogger(__name__)


def embedding_configured() -> bool:
    s = get_settings()
    return bool((s.embedding_base_url or "").strip() and (s.embedding_api_key or "").strip())


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """Return embedding vectors for each text. Raises on HTTP/config errors."""
    if not texts:
        return []
    s = get_settings()
    if not embedding_configured():
        raise RuntimeError("未配置 Embedding API（EMBEDDING_BASE_URL / EMBEDDING_API_KEY）")
    base = normalize_openai_base_url(s.embedding_base_url, "openai")
    url = f"{base.rstrip('/')}/embeddings"
    headers = {
        "Authorization": f"Bearer {s.embedding_api_key.strip()}",
        "Content-Type": "application/json",
    }
    payload = {"model": s.embedding_model or "text-embedding-3-small", "input": texts}
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
    items = data.get("data") or []
    items = sorted(items, key=lambda x: x.get("index", 0))
    vectors = [list(item.get("embedding") or []) for item in items]
    if len(vectors) != len(texts):
        raise RuntimeError(f"embedding 返回数量不匹配: expect {len(texts)} got {len(vectors)}")
    return vectors


def embed_texts_sync(texts: list[str]) -> list[list[float]]:
    """Sync wrapper for indexer (runs in request thread)."""
    if not texts:
        return []
    s = get_settings()
    if not embedding_configured():
        raise RuntimeError("未配置 Embedding API（EMBEDDING_BASE_URL / EMBEDDING_API_KEY）")
    base = normalize_openai_base_url(s.embedding_base_url, "openai")
    url = f"{base.rstrip('/')}/embeddings"
    headers = {
        "Authorization": f"Bearer {s.embedding_api_key.strip()}",
        "Content-Type": "application/json",
    }
    payload = {"model": s.embedding_model or "text-embedding-3-small", "input": texts}
    with httpx.Client(timeout=120.0) as client:
        resp = client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
    items = sorted(data.get("data") or [], key=lambda x: x.get("index", 0))
    vectors = [list(item.get("embedding") or []) for item in items]
    if len(vectors) != len(texts):
        raise RuntimeError(f"embedding 返回数量不匹配: expect {len(texts)} got {len(vectors)}")
    return vectors
