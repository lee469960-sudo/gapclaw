import json
import shutil
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.deps import get_session_user
from app.models import User, FileShare
from app.schemas import ok, fail
from app.security import new_token, now_str
from app.services import chunk_upload
from app.services.file_preview import preview_file

router = APIRouter(prefix="/pages/page_files.cgi", tags=["files"])


class FileBody(BaseModel):
    action: str | None = None
    path: str = ""
    name: str | None = None
    new_name: str | None = None
    dest: str | None = None
    items: list[dict] | None = None
    visibility: str = "private"
    allowed_users: list[str] | None = None
    upload_id: str | None = None
    chunk_index: int | None = None
    total_chunks: int | None = None
    filename: str | None = None
    token: str | None = None


def _files_root() -> Path:
    root = Path(get_settings().data_dir) / "files"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_path(rel: str) -> Path:
    root = _files_root().resolve()
    target = (root / rel.lstrip("/")).resolve()
    if not str(target).startswith(str(root)):
        raise ValueError("非法路径")
    return target


def _list_entries(rel: str) -> list[dict]:
    target = _safe_path(rel)
    target.mkdir(parents=True, exist_ok=True)
    entries = []
    for item in sorted(target.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
        st = item.stat()
        entries.append({
            "name": item.name,
            "is_dir": item.is_dir(),
            "size": st.st_size,
            "modified": "",
            "path": str((Path(rel) / item.name).as_posix()).lstrip("/"),
        })
    return entries


@router.get("")
async def files_get(
    action: str = Query("list"),
    path: str = Query(""),
    token: str = Query(None),
    user: User = Depends(get_session_user),
    db: Session = Depends(get_db),
):
    try:
        if action == "list":
            return ok({"entries": _list_entries(path), "path": path})
        if action == "download":
            target = _safe_path(path)
            if target.is_file():
                return FileResponse(target, filename=target.name)
            return fail("不是文件")
        if action == "preview":
            target = _safe_path(path)
            return ok(preview_file(target))
        if action == "resolve_share" and token:
            share = db.query(FileShare).filter(FileShare.share_token == token).first()
            if not share:
                return fail("分享不存在")
            if share.visibility == "private" and user.username not in json.loads(share.allowed_users or "[]") and share.creator != user.username:
                roles = json.loads(user.roles or "[]")
                if "admin" not in roles and "master" not in roles:
                    return fail("无权限")
            target = _safe_path(share.path)
            if target.is_file():
                return FileResponse(target, filename=share.name or target.name)
            return ok({"path": share.path, "name": share.name})
    except ValueError as e:
        return fail(str(e))
    return fail("未知操作")


@router.post("")
async def files_post(
    request: Request,
    user: User = Depends(get_session_user),
    db: Session = Depends(get_db),
):
    content_type = request.headers.get("content-type", "")

    try:
        if "multipart/form-data" in content_type:
            form = await request.form()
            act = form.get("action") or "upload"
            rel = form.get("path") or ""

            if act == "upload_chunk":
                upload = form.get("file")
                if not upload or not hasattr(upload, "file"):
                    return fail("缺少分片文件")
                data = await upload.read()
                chunk_upload.save_chunk(form.get("upload_id") or "", int(form.get("chunk_index") or 0), data)
                return ok({"received": True})

            upload = form.get("file")
            if act == "upload" or upload:
                if not upload or not hasattr(upload, "file"):
                    return fail("请选择文件")
                filename = upload.filename or "upload"
                target = _safe_path(f"{rel.rstrip('/')}/{filename}")
                target.parent.mkdir(parents=True, exist_ok=True)
                with open(target, "wb") as f:
                    shutil.copyfileobj(upload.file, f)
                return ok({"name": filename}, "上传成功")

            return fail("未知操作")

        body = FileBody(**await request.json())
        act = body.action or "list"
        rel = body.path or ""

        if act == "list":
            return ok({"entries": _list_entries(rel), "path": rel})

        if act == "batch_delete" and body.items:
            for item in body.items:
                target = _safe_path(item.get("path", ""))
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink(missing_ok=True)
            return ok(None, "批量删除成功")

        if act == "upload_chunk_init":
            uid = body.upload_id or new_token()
            chunk_upload.init_upload(uid, body.filename or "upload", body.total_chunks or 1)
            return ok({"upload_id": uid})

        if act == "upload_chunk_merge":
            dest = _safe_path(f"{rel.rstrip('/')}/{body.filename or 'merged'}")
            chunk_upload.merge_chunks(body.upload_id or "", dest)
            return ok({"path": str(dest.relative_to(_files_root()))}, "合并成功")

        if act == "mkdir":
            if not body.name:
                return fail("缺少文件夹名称")
            _safe_path(f"{rel.rstrip('/')}/{body.name}").mkdir(parents=True, exist_ok=True)
            return ok(None, "创建成功")

        if act == "rename":
            if not body.name or not body.new_name:
                return fail("缺少名称参数")
            src = _safe_path(f"{rel.rstrip('/')}/{body.name}")
            dst = _safe_path(f"{rel.rstrip('/')}/{body.new_name}")
            src.rename(dst)
            return ok(None, "重命名成功")

        if act == "delete":
            if not body.name:
                return fail("缺少名称参数")
            target = _safe_path(f"{rel.rstrip('/')}/{body.name}")
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink(missing_ok=True)
            return ok(None, "删除成功")

        if act == "move":
            if not body.name or not body.dest:
                return fail("缺少参数")
            src = _safe_path(f"{rel.rstrip('/')}/{body.name}")
            dst = _safe_path(body.dest)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            return ok(None, "移动成功")

        if act == "zip":
            target = _safe_path(rel)
            zip_path = target.with_suffix(".zip")
            with zipfile.ZipFile(zip_path, "w") as zf:
                if target.is_dir():
                    for f in target.rglob("*"):
                        if f.is_file():
                            zf.write(f, f.relative_to(target.parent))
                else:
                    zf.write(target, target.name)
            return ok({"zip": str(zip_path.name)})

        if act == "unzip":
            target = _safe_path(rel)
            with zipfile.ZipFile(target, "r") as zf:
                zf.extractall(target.parent)
            return ok(None, "解压成功")

        if act == "share":
            share = FileShare(
                path=rel,
                name=body.name or rel,
                share_token=new_token(),
                visibility=body.visibility,
                allowed_users=json.dumps(body.allowed_users or []),
                creator=user.username,
                created_at=now_str(),
            )
            db.add(share)
            db.commit()
            return ok({"token": share.share_token})

        if act == "set_perm":
            share = db.query(FileShare).filter(FileShare.path == rel).first()
            if not share:
                share = FileShare(path=rel, name=rel, share_token=new_token(), creator=user.username, created_at=now_str())
                db.add(share)
            share.visibility = body.visibility
            share.allowed_users = json.dumps(body.allowed_users or [])
            db.commit()
            return ok(None, "权限已更新")

    except ValueError as e:
        return fail(str(e))
    except Exception as e:
        return fail(str(e))

    return fail("未知操作")
