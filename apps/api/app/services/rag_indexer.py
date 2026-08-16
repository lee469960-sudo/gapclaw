import json
import math
import re
from pathlib import Path

from sqlalchemy import or_, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import RagChunk, RagCorpus
from app.services.rag_extract import RagExtractError, extract_text


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.lower()))


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> list[str]:
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]
    step = max(chunk_size - overlap, 1)
    chunks = []
    for i in range(0, len(text), step):
        part = text[i : i + chunk_size]
        if part.strip():
            chunks.append(part)
        if i + chunk_size >= len(text):
            break
    return chunks or [text]


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / (na * nb)


def _parse_vec(raw: str | None) -> list[float]:
    if not raw:
        return []
    try:
        v = json.loads(raw)
        if isinstance(v, list) and v and isinstance(v[0], (int, float)):
            return [float(x) for x in v]
    except Exception:
        pass
    return []


def _pgvector_available(db: Session) -> bool:
    bind = db.get_bind()
    if bind.dialect.name != "postgresql":
        return False
    try:
        row = db.execute(text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")).first()
        return bool(row)
    except Exception:
        return False


def index_corpus(db: Session, corpus: RagCorpus, chunk_size: int = 500) -> int:
    db.query(RagChunk).filter(RagChunk.corpus_id == corpus.id).delete()
    corpus.index_status = "pending"
    corpus.index_error = ""
    if not corpus.file_path or not Path(corpus.file_path).exists():
        corpus.chunk_count = 0
        corpus.index_status = "error"
        corpus.index_error = "文件不存在"
        db.commit()
        return 0
    try:
        text_body = extract_text(corpus.file_path)
    except RagExtractError as e:
        corpus.chunk_count = 0
        corpus.index_status = "error"
        corpus.index_error = str(e)
        db.commit()
        raise

    chunks = _chunk_text(text_body, chunk_size=chunk_size, overlap=50)
    vectors: list[list[float]] = [[] for _ in chunks]
    from app.services.embedding_client import embedding_configured, embed_texts_sync

    if embedding_configured() and chunks:
        try:
            batch_size = 32
            out: list[list[float]] = []
            for i in range(0, len(chunks), batch_size):
                out.extend(embed_texts_sync(chunks[i : i + batch_size]))
            vectors = out
        except Exception as e:
            corpus.index_error = f"embedding 失败，已回退词法索引: {e}"

    for i, chunk in enumerate(chunks):
        tokens = list(_tokenize(chunk))
        vec = vectors[i] if i < len(vectors) else []
        db.add(
            RagChunk(
                corpus_id=corpus.id,
                text=chunk,
                embedding=json.dumps(tokens),
                embedding_vec=json.dumps(vec) if vec else "",
                chunk_index=i,
            )
        )
    corpus.chunk_count = len(chunks)
    if any(vectors):
        corpus.index_status = "ready"
        if not corpus.index_error:
            corpus.index_error = ""
    else:
        corpus.index_status = "ready" if chunks else "error"
        if not chunks:
            corpus.index_error = corpus.index_error or "无文本分片"
    db.commit()
    return len(chunks)


def _lexical_search(
    db: Session,
    query: str,
    corpus_ids: list[str] | None,
    top_k: int,
) -> list[dict]:
    q_tokens = _tokenize(query)
    settings = get_settings()
    candidate_limit = max(int(settings.rag_candidate_limit or 2000), top_k)

    q = db.query(RagChunk)
    if corpus_ids is not None:
        if not corpus_ids:
            return []
        q = q.filter(RagChunk.corpus_id.in_(corpus_ids))

    tokens = [t for t in q_tokens if len(t) >= 2] or list(q_tokens)
    if tokens:
        likes = [RagChunk.text.ilike(f"%{t}%") for t in list(tokens)[:8]]
        candidates = q.filter(or_(*likes)).limit(candidate_limit).all()
        if not candidates and query.strip():
            candidates = q.filter(RagChunk.text.ilike(f"%{query.strip()[:80]}%")).limit(candidate_limit).all()
    else:
        candidates = q.limit(candidate_limit).all()

    results = []
    q_lower = query.lower()
    for chunk in candidates:
        text_lower = (chunk.text or "").lower()
        tokens_set = set(json.loads(chunk.embedding or "[]"))
        score = _jaccard(q_tokens, tokens_set)
        if q_lower and q_lower in text_lower:
            score = max(score, 0.15)
        else:
            for t in q_tokens:
                if len(t) >= 2 and t in text_lower:
                    score = max(score, 0.1)
                    break
        if score > 0:
            results.append(
                {
                    "corpus_id": chunk.corpus_id,
                    "snippet": (chunk.text or "")[:300],
                    "score": round(score, 3),
                }
            )
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top_k]


def _vector_search_python(
    db: Session,
    query_vec: list[float],
    corpus_ids: list[str] | None,
    top_k: int,
) -> list[dict]:
    q = db.query(RagChunk).filter(RagChunk.embedding_vec.isnot(None), RagChunk.embedding_vec != "")
    if corpus_ids is not None:
        if not corpus_ids:
            return []
        q = q.filter(RagChunk.corpus_id.in_(corpus_ids))
    limit = max(get_settings().rag_candidate_limit or 2000, top_k)
    rows = q.limit(limit).all()
    scored = []
    for chunk in rows:
        vec = _parse_vec(chunk.embedding_vec)
        if not vec:
            continue
        score = _cosine(query_vec, vec)
        if score > 0:
            scored.append(
                {
                    "corpus_id": chunk.corpus_id,
                    "snippet": (chunk.text or "")[:300],
                    "score": round(score, 3),
                }
            )
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_k]


def _vector_search_pg(
    db: Session,
    query_vec: list[float],
    corpus_ids: list[str] | None,
    top_k: int,
) -> list[dict] | None:
    if not _pgvector_available(db):
        return None
    qvec = "[" + ",".join(str(float(x)) for x in query_vec) + "]"
    params: dict = {"qvec": qvec, "k": top_k}
    where = "embedding_vec IS NOT NULL AND embedding_vec <> ''"
    if corpus_ids is not None:
        if not corpus_ids:
            return []
        where += " AND corpus_id = ANY(:ids)"
        params["ids"] = corpus_ids
    sql = text(
        f"""
        SELECT corpus_id, left(text, 300) AS snippet,
               1 - (embedding_vec::vector <=> CAST(:qvec AS vector)) AS score
        FROM rag_chunks
        WHERE {where}
        ORDER BY embedding_vec::vector <=> CAST(:qvec AS vector)
        LIMIT :k
        """
    )
    try:
        rows = db.execute(sql, params).mappings().all()
    except Exception:
        return None
    return [
        {
            "corpus_id": r["corpus_id"],
            "snippet": r["snippet"] or "",
            "score": round(float(r["score"] or 0), 3),
        }
        for r in rows
    ]


def search_corpus(
    db: Session,
    query: str,
    corpus_ids: list[str] | None = None,
    top_k: int = 5,
) -> list[dict]:
    query = (query or "").strip()
    if not query:
        return []

    from app.services.embedding_client import embedding_configured, embed_texts_sync

    if embedding_configured():
        try:
            qvec = embed_texts_sync([query])[0]
            if qvec:
                pg_hits = _vector_search_pg(db, qvec, corpus_ids, top_k)
                if pg_hits is not None:
                    return pg_hits
                hits = _vector_search_python(db, qvec, corpus_ids, top_k)
                if hits:
                    return hits
        except Exception:
            pass

    return _lexical_search(db, query, corpus_ids, top_k)
