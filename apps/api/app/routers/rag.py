import json
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.deps import can_access_resource, get_session_user
from app.models import User, RagCorpus, RagChunk
from app.schemas import ok, fail
from app.security import new_id, now_str
from app.services.rag_extract import RagExtractError, SUPPORTED_SUFFIXES
from app.services.rag_indexer import index_corpus, search_corpus

router = APIRouter(prefix="/pages/page_rag.cgi", tags=["rag"])


class RagBody(BaseModel):
    action: str | None = None
    id: str | None = None
    name: str | None = None
    description: str = ""
    query: str = ""
    visibility: str = "private"
    allowed_users: list[str] | None = None
    corpus_ids: list[str] | None = None


def _visible_corpora(db: Session, user: User) -> list[RagCorpus]:
    return [
        x
        for x in db.query(RagCorpus).all()
        if can_access_resource(user, x.visibility, x.allowed_users, x.creator)
    ]


def _filter_corpus_ids(db: Session, user: User, corpus_ids: list[str] | None) -> list[str]:
    visible = {c.id for c in _visible_corpora(db, user)}
    if corpus_ids is None:
        return list(visible)
    return [cid for cid in corpus_ids if cid in visible]


def _delete_corpus(db: Session, r: RagCorpus) -> None:
    db.query(RagChunk).filter(RagChunk.corpus_id == r.id).delete(synchronize_session=False)
    if r.file_path:
        try:
            Path(r.file_path).unlink(missing_ok=True)
        except OSError:
            pass
    db.delete(r)


def _save_corpus_file(r: RagCorpus, upload_file, db: Session) -> None:
    settings = get_settings()
    filename = getattr(upload_file, "filename", None) or "upload.bin"
    suffix = Path(filename).suffix.lower()
    if suffix == ".doc":
        raise RagExtractError("不支持 .doc，请转换为 .docx / .pdf / .txt 后上传")
    if suffix and suffix not in SUPPORTED_SUFFIXES:
        raise RagExtractError(f"不支持的文件类型: {suffix}")

    # Size limit: prefer SpooledTemporaryFile size if available
    max_bytes = int(settings.rag_max_upload_mb or 32) * 1024 * 1024
    raw = upload_file.file
    raw.seek(0, 2)
    size = raw.tell()
    raw.seek(0)
    if size > max_bytes:
        raise RagExtractError(f"文件超过上限 {settings.rag_max_upload_mb}MB")

    dest = Path(settings.upload_dir) / "rag" / f"{r.id}_{filename}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "wb") as f:
        shutil.copyfileobj(raw, f)
    r.file_path = str(dest)
    r.chunk_count = index_corpus(db, r)


@router.get("")
async def rag_get(
    action: str = Query("list"),
    user: User = Depends(get_session_user),
    db: Session = Depends(get_db),
):
    if action == "list":
        return ok([x.to_dict() for x in _visible_corpora(db, user)])
    return fail("未知操作")


@router.post("")
async def rag_post(request: Request, user: User = Depends(get_session_user), db: Session = Depends(get_db)):
    content_type = request.headers.get("content-type", "")

    if "multipart/form-data" in content_type:
        form = await request.form()
        rid = form.get("corpus_id") or new_id()
        r = db.query(RagCorpus).filter(RagCorpus.id == rid).first()
        created = False
        if not r:
            r = RagCorpus(id=rid, creator=user.username, index_status="pending")
            db.add(r)
            created = True
        elif not can_access_resource(user, r.visibility, r.allowed_users, r.creator):
            return fail("无权限")
        r.name = form.get("name") or r.name or "未命名"
        r.description = form.get("description") or r.description or ""
        r.visibility = form.get("visibility") or r.visibility or "private"
        upload = form.get("file")
        try:
            if upload and hasattr(upload, "file"):
                _save_corpus_file(r, upload, db)
        except RagExtractError as e:
            if created:
                db.rollback()
                existing = db.query(RagCorpus).filter(RagCorpus.id == rid).first()
                if existing and not existing.file_path:
                    db.delete(existing)
                    db.commit()
            return fail(str(e))
        except Exception as e:
            r.index_status = "error"
            r.index_error = str(e)
            db.commit()
            return fail(f"索引失败: {e}")
        r.modified_at = now_str()
        db.commit()
        return ok(r.to_dict(), "保存成功")

    body = RagBody(**await request.json())
    act = body.action or "list"

    if act == "list":
        return ok([x.to_dict() for x in _visible_corpora(db, user)])

    if act == "create":
        rid = body.id or new_id()
        r = db.query(RagCorpus).filter(RagCorpus.id == rid).first()
        if not r:
            r = RagCorpus(id=rid, creator=user.username, index_status="pending")
            db.add(r)
        r.name = body.name or r.name or "未命名"
        r.description = body.description or ""
        r.visibility = body.visibility or "private"
        r.modified_at = now_str()
        db.commit()
        return ok(r.to_dict(), "保存成功")

    if act == "reindex":
        if not body.id:
            return fail("缺少 id")
        r = db.query(RagCorpus).filter(RagCorpus.id == body.id).first()
        if not r:
            return fail("不存在")
        if not can_access_resource(user, r.visibility, r.allowed_users, r.creator):
            return fail("无权限")
        try:
            count = index_corpus(db, r)
        except RagExtractError as e:
            return fail(str(e))
        except Exception as e:
            r.index_status = "error"
            r.index_error = str(e)
            db.commit()
            return fail(f"索引失败: {e}")
        return ok({"chunk_count": count, "index_status": r.index_status, "index_error": r.index_error}, "索引完成")

    if act == "delete":
        if not body.id:
            return fail("缺少 id")
        r = db.query(RagCorpus).filter(RagCorpus.id == body.id).first()
        if not r:
            return ok(None, "删除成功")
        if not can_access_resource(user, r.visibility, r.allowed_users, r.creator):
            return fail("无权限")
        _delete_corpus(db, r)
        db.commit()
        return ok(None, "删除成功")

    if act == "search":
        ids = _filter_corpus_ids(db, user, body.corpus_ids)
        results = search_corpus(db, body.query or "", ids, top_k=10)
        return ok(results)

    return fail("未知操作")
