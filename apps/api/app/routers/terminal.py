import json
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import can_access_resource, get_session_user
from app.models import User, SshServer
from app.schemas import ok, fail
from app.security import encrypt_secret, new_id

router = APIRouter(prefix="/pages/page_terminal.cgi", tags=["terminal"])


class TerminalBody(BaseModel):
    action: str | None = None
    id: str | None = None
    scope: str | None = None
    name: str | None = None
    host: str = ""
    port: int = 22
    username: str = "root"
    auth_type: str = "password"
    password: str = ""
    private_key: str = ""
    passphrase: str = ""
    description: str = ""
    visibility: str = "private"
    allowed_users: list[str] | None = None
    cwd: str = "/"
    path: str = ""
    files: list[str] | None = None


def _filter_servers(items: list[SshServer], user: User, scope: str = "all") -> list[SshServer]:
    visible = [s for s in items if can_access_resource(user, s.visibility, s.allowed_users, s.creator)]
    if scope == "mine":
        return [s for s in visible if s.creator == user.username]
    return visible


def _accessible_server(db: Session, user: User, server_id: str | None) -> SshServer | None:
    if not server_id:
        return None
    s = db.query(SshServer).filter(SshServer.id == server_id).first()
    if not s or not can_access_resource(user, s.visibility, s.allowed_users, s.creator):
        return None
    return s


def _download_response(data: bytes, remote_path: str) -> Response:
    name = Path(remote_path.rstrip("/") or "download").name or "download"
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}",
        },
    )


def _filter_servers(items: list[SshServer], user: User, scope: str = "all") -> list[SshServer]:
    visible = [s for s in items if can_access_resource(user, s.visibility, s.allowed_users, s.creator)]
    if scope == "mine":
        return [s for s in visible if s.creator == user.username]
    return visible


def _server_from_body(body: TerminalBody, existing: SshServer | None = None) -> SshServer:
    s = existing or SshServer()
    if body.host:
        s.host = body.host
    if body.port:
        s.port = body.port
    if body.username:
        s.username = body.username
    if body.auth_type:
        s.auth_type = body.auth_type
    if body.password:
        s.password_enc = encrypt_secret(body.password)
    elif existing:
        s.password_enc = existing.password_enc
    if body.private_key:
        s.private_key_enc = encrypt_secret(body.private_key)
    elif existing:
        s.private_key_enc = existing.private_key_enc
    if body.passphrase:
        s.passphrase_enc = encrypt_secret(body.passphrase)
    elif existing:
        s.passphrase_enc = existing.passphrase_enc
    return s


@router.get("")
async def terminal_get(
    action: str = Query("list"),
    id: str = Query(None),
    scope: str = Query("all"),
    path: str = Query(""),
    user: User = Depends(get_session_user),
    db: Session = Depends(get_db),
):
    if action == "list":
        items = _filter_servers(db.query(SshServer).order_by(SshServer.name).all(), user, scope)
        return ok([s.to_dict() for s in items])
    if action == "get" and id:
        s = _accessible_server(db, user, id)
        if not s:
            return fail("不存在或无权限")
        return ok(s.to_dict())
    if action == "sftp_list" and id:
        from app.services.ssh_service import sftp_list
        s = _accessible_server(db, user, id)
        if not s:
            return fail("不存在或无权限")
        return ok(sftp_list(s, path or "/"))
    if action == "sftp_download" and id:
        from app.services.ssh_service import sftp_download
        s = _accessible_server(db, user, id)
        if not s:
            return fail("不存在或无权限")
        remote = (path or "").strip()
        if not remote:
            return fail("请输入远程文件路径")
        try:
            data = sftp_download(s, remote)
        except Exception as exc:
            return fail(str(exc) or "下载失败")
        return _download_response(data, remote)
    return fail("未知操作")


@router.post("")
async def terminal_post(request: Request, user: User = Depends(get_session_user), db: Session = Depends(get_db)):
    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" in content_type:
        form = await request.form()
        act = form.get("action")
        if act != "sftp_upload":
            return fail("未知操作")
        s = _accessible_server(db, user, str(form.get("id") or ""))
        if not s:
            return fail("不存在或无权限")
        file = form.get("file")
        if not file or not hasattr(file, "read"):
            return fail("请选择文件")
        from app.services.ssh_service import sftp_upload

        data = await file.read()
        filename = getattr(file, "filename", None) or "upload.bin"
        remote_dir = str(form.get("path") or form.get("cwd") or ".")
        try:
            dest = sftp_upload(s, remote_dir, data, filename)
        except Exception as exc:
            return fail(str(exc) or "上传失败")
        return ok({"path": dest}, f"已上传到 {dest}")

    try:
        payload = await request.json()
    except Exception:
        return fail("请求格式错误")
    body = TerminalBody(**payload)
    action = body.action or "create"

    if action == "list":
        items = _filter_servers(db.query(SshServer).order_by(SshServer.name).all(), user, body.scope or "all")
        return ok([s.to_dict() for s in items])

    if action in ("create", "update"):
        if not body.name or not body.host or not body.username:
            return fail("请填写名称、主机和用户名")
        if body.id:
            s = db.query(SshServer).filter(SshServer.id == body.id).first()
            if not s:
                return fail("不存在")
            if s.creator != user.username and "admin" not in json.loads(user.roles or "[]") and "master" not in json.loads(user.roles or "[]"):
                return fail("无权限编辑")
        else:
            s = SshServer(id=new_id(), creator=user.username)
            db.add(s)
        s.name = body.name.strip()
        s.host = body.host.strip()
        s.port = body.port or 22
        s.username = body.username.strip()
        s.auth_type = body.auth_type or "password"
        if body.password:
            s.password_enc = encrypt_secret(body.password)
        if body.private_key:
            s.private_key_enc = encrypt_secret(body.private_key)
        if body.passphrase:
            s.passphrase_enc = encrypt_secret(body.passphrase)
        s.description = body.description or ""
        s.visibility = body.visibility or "private"
        s.allowed_users = json.dumps(body.allowed_users or [])
        db.commit()
        return ok(s.to_dict(), "保存成功")

    if action == "delete":
        s = db.query(SshServer).filter(SshServer.id == body.id).first()
        if not s:
            return fail("不存在")
        if s.creator != user.username and "admin" not in json.loads(user.roles or "[]") and "master" not in json.loads(user.roles or "[]"):
            return fail("无权限删除")
        db.query(SshServer).filter(SshServer.id == body.id).delete()
        db.commit()
        return ok(None, "删除成功")

    if action == "test":
        from app.services.ssh_service import test_ssh
        if body.id and not body.host:
            s = db.query(SshServer).filter(SshServer.id == body.id).first()
            if not s:
                return fail("不存在")
            if not can_access_resource(user, s.visibility, s.allowed_users, s.creator):
                return fail("无权限")
        else:
            if not body.host or not body.username:
                return fail("请填写主机和用户名")
            s = _server_from_body(body)
        result = test_ssh(s)
        return ok(result) if result.get("connected") else fail(result.get("error", "连接失败"))

    if action == "sftp_list":
        from app.services.ssh_service import sftp_list
        s = _accessible_server(db, user, body.id)
        if not s:
            return fail("不存在或无权限")
        cwd = body.path or body.cwd or "/"
        return ok({"files": sftp_list(s, cwd), "cwd": cwd})

    if action == "sftp_download":
        from app.services.ssh_service import sftp_download
        s = _accessible_server(db, user, body.id)
        if not s:
            return fail("不存在或无权限")
        remote = (body.path or body.cwd or "").strip()
        if not remote:
            return fail("请输入远程文件路径")
        try:
            data = sftp_download(s, remote)
        except Exception as exc:
            return fail(str(exc) or "下载失败")
        return _download_response(data, remote)

    if action == "sftp_upload":
        return fail("请使用表单上传文件")

    if action == "sftp_mkdir":
        from app.services.ssh_service import sftp_mkdir
        s = _accessible_server(db, user, body.id)
        if not s:
            return fail("不存在或无权限")
        sftp_mkdir(s, body.path or body.cwd or "/")
        return ok(None, "创建成功")

    if action == "sftp_delete":
        from app.services.ssh_service import sftp_delete
        s = _accessible_server(db, user, body.id)
        if not s:
            return fail("不存在或无权限")
        sftp_delete(s, body.path or body.cwd or "/")
        return ok(None, "删除成功")

    return fail("未知操作")
