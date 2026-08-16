import json
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.config import get_settings
from app.database import get_db
from app.deps import can_access_resource, get_session_user
from app.models import User, Sandbox
from app.schemas import ok, fail
from app.security import new_id, now_str
from app.services import docker_service

router = APIRouter(prefix="/pages/page_sandbox.cgi", tags=["sandbox"])


def _accessible_sandbox(db: Session, user: User, sandbox_id: str) -> Sandbox | None:
    s = db.query(Sandbox).filter(Sandbox.id == sandbox_id).first()
    if not s:
        return None
    if not can_access_resource(user, s.visibility, s.allowed_users, s.creator):
        return None
    return s


def _normalize_wp_rel(path: str | None) -> str:
    """Strip leading /workplace so paths match host workplace root."""
    p = (path or "").strip().lstrip("/")
    if p.startswith("workplace/"):
        p = p[len("workplace/") :]
    elif p == "workplace":
        p = ""
    return p


class SandboxBody(BaseModel):
    action: str | None = None
    id: str | None = None
    name: str | None = None
    image: str | None = None
    cpu_count: int = 2
    memory_mb: int = 512
    network_mode: str = "bridge"
    port_mappings: str = ""
    visibility: str = "private"
    allowed_users: list[str] | None = None
    image_name: str | None = None
    image_tag: str = "latest"
    docker_socket: str | None = None
    shell: str = "/bin/bash"
    scope: str | None = None


def _filter_sandboxes(items: list[Sandbox], user: User, scope: str = "all") -> list[Sandbox]:
    visible = [s for s in items if can_access_resource(user, s.visibility, s.allowed_users, s.creator)]
    if scope == "mine":
        return [s for s in visible if s.creator == user.username]
    return visible


def _sync_and_serialize(db: Session, items: list[Sandbox]) -> list[dict]:
    ids = [s.container_id for s in items if s.container_id]
    statuses = docker_service.sync_containers_status(ids)
    changed = False
    for s in items:
        if not s.container_id:
            continue
        status = statuses.get(s.container_id)
        if status and status != s.status:
            s.status = status
            changed = True
    if changed:
        db.commit()
    return [s.to_dict() for s in items]


@router.get("")
async def sandbox_get(
    action: str = Query("list"),
    id: str = Query(None),
    image: str = Query(None),
    scope: str = Query("all"),
    upload_id: str = Query(None),
    path: str = Query(None),
    user: User = Depends(get_session_user),
    db: Session = Depends(get_db),
):
    if action == "list_refs":
        items = _filter_sandboxes(db.query(Sandbox).all(), user, scope)
        return ok([{"id": s.id, "name": s.name} for s in items])
    if action == "list":
        items = _filter_sandboxes(db.query(Sandbox).all(), user, scope)
        return ok(_sync_and_serialize(db, items))
    if action == "get" and id:
        s = db.query(Sandbox).filter(Sandbox.id == id).first()
        if not s:
            return fail("不存在")
        return ok(s.to_dict())
    if action == "docker_info":
        return ok(docker_service.docker_info())
    if action == "images":
        return ok(docker_service.list_images())
    if action == "export_image" and image:
        path = docker_service.export_image(image)
        if not path:
            return fail("导出失败")
        safe_name = image.replace(":", "_").replace("/", "_")
        return FileResponse(
            path,
            filename=f"{safe_name}.tar",
            media_type="application/x-tar",
            background=BackgroundTask(lambda: path.unlink(missing_ok=True)),
        )
    if action == "get_setting":
        settings = get_settings()
        return ok({
            "docker_socket": settings.docker_socket,
            "default_sandbox_image": settings.default_sandbox_image,
        })
    if action == "size" and id:
        wp = Path(get_settings().workplace_dir) / id / "workplace"
        total = sum(f.stat().st_size for f in wp.rglob("*") if f.is_file()) if wp.exists() else 0
        return ok({"bytes": total})
    if action == "download_workplace" and id:
        s = _accessible_sandbox(db, user, id)
        if not s:
            return fail("沙箱不存在或无权限")
        from app.services.workplace import download_path

        fp = download_path(s.id, _normalize_wp_rel(path))
        if fp:
            return FileResponse(fp, filename=fp.name)
        return fail("文件不存在")
    return fail("未知操作")


@router.post("")
async def sandbox_post(
    request: Request,
    user: User = Depends(get_session_user),
    db: Session = Depends(get_db),
):
    content_type = request.headers.get("content-type", "")

    if "multipart/form-data" in content_type:
        form = await request.form()
        act = form.get("action")
        if act == "import_image":
            file = form.get("file")
            if file and hasattr(file, "read"):
                data = await file.read()
                tags = docker_service.import_image(data)
                if not tags:
                    return fail("导入失败，请确认 Docker 可用且文件为有效 tar 镜像")
                return ok({"tags": tags}, "导入成功")
        if act == "upload_workplace":
            sandbox_id = form.get("id") or form.get("sandbox_id")
            if not sandbox_id:
                return fail("缺少沙箱 id")
            s = _accessible_sandbox(db, user, str(sandbox_id))
            if not s:
                return fail("沙箱不存在或无权限")
            file = form.get("file")
            if not file or not hasattr(file, "read"):
                return fail("请选择文件")
            from app.services.workplace import upload_file

            data = await file.read()
            rel_dir = _normalize_wp_rel(str(form.get("path") or ""))
            result = upload_file(s.id, rel_dir, getattr(file, "filename", None) or "upload.bin", data)
            if not result.get("ok"):
                return fail(result.get("msg", "上传失败"))
            return ok({"path": result["path"]}, "上传成功")
        return fail("未知操作")

    try:
        payload = await request.json()
    except Exception:
        return fail("请求格式错误")

    body = SandboxBody(**payload)
    action = body.action or "create"
    settings = get_settings()

    if action == "list_refs":
        items = _filter_sandboxes(db.query(Sandbox).all(), user, body.scope or "all")
        return ok([{"id": s.id, "name": s.name} for s in items])
    if action == "list":
        items = _filter_sandboxes(db.query(Sandbox).all(), user, body.scope or "all")
        return ok(_sync_and_serialize(db, items))

    if action in ("create", "update"):
        if body.id:
            s = db.query(Sandbox).filter(Sandbox.id == body.id).first()
            if not s:
                return fail("不存在")
        else:
            s = Sandbox(id=new_id(), creator=user.username, created_at=now_str())
            db.add(s)
        s.name = body.name or s.name or "未命名"
        s.image = body.image or settings.default_sandbox_image
        s.cpu_count = body.cpu_count
        s.memory_mb = body.memory_mb
        # 创建后网络不可修改（编辑/重建保持原值）
        if not body.id:
            s.network_mode = body.network_mode or "bridge"
        s.port_mappings = body.port_mappings
        s.visibility = body.visibility
        s.allowed_users = json.dumps(body.allowed_users or [])
        s.updated_at = now_str()
        db.commit()
        from app.services.workplace import ensure_workplace
        ensure_workplace(s.id)
        return ok(s.to_dict(), "保存成功")

    if action == "start":
        s = db.query(Sandbox).filter(Sandbox.id == body.id).first()
        if not s:
            return fail("不存在")
        try:
            cid, status, err = docker_service.start_sandbox(s)
        except Exception as e:
            return fail(f"启动失败: {docker_service._format_docker_error(e)}")
        if status == "error" or not cid:
            return fail(err or "启动失败，请检查 Docker 是否运行及镜像是否存在")
        s.container_id = cid
        s.container_name = f"gap-sandbox-{s.id}"
        s.status = status
        s.updated_at = now_str()
        db.commit()
        return ok(s.to_dict(), "启动成功")

    if action == "stop":
        s = db.query(Sandbox).filter(Sandbox.id == body.id).first()
        if not s:
            return fail("不存在")
        docker_service.stop_sandbox(s.container_id)
        s.status = "stopped"
        s.updated_at = now_str()
        db.commit()
        return ok(s.to_dict(), "已停止")

    if action == "rebuild":
        s = db.query(Sandbox).filter(Sandbox.id == body.id).first()
        if not s:
            return fail("不存在")
        docker_service.destroy_sandbox(s.container_id, s.container_name)
        cid, status, err = docker_service.start_sandbox(s)
        if status == "error" or not cid:
            return fail(err or "重建失败")
        s.container_id = cid
        s.container_name = f"gap-sandbox-{s.id}"
        s.status = status
        s.updated_at = now_str()
        db.commit()
        return ok(s.to_dict(), "重建成功")

    if action == "destroy":
        s = db.query(Sandbox).filter(Sandbox.id == body.id).first()
        if not s:
            return fail("不存在")
        docker_service.destroy_sandbox(s.container_id, s.container_name)
        db.delete(s)
        db.commit()
        return ok(None, "已销毁")

    if action == "commit_image":
        s = db.query(Sandbox).filter(Sandbox.id == body.id).first()
        if not s:
            return fail("不存在")
        if not s.container_id:
            return fail("沙箱未启动，请先启动后再保存镜像")
        status = docker_service.sync_container_status(s.container_id)
        if status != "running":
            s.status = status or "stopped"
            db.commit()
            return fail("沙箱未运行，请先启动后再保存镜像")
        repo = docker_service.sanitize_image_repo(body.image_name or f"gap-{s.name}")
        tag = (body.image_tag or "latest").strip() or "latest"
        image_ref, result = docker_service.commit_image(s.container_id, repo, tag)
        if not image_ref:
            return fail(result)
        return ok({"image_id": result, "image": image_ref}, f"已保存到镜像库: {image_ref}")

    if action == "pull_image":
        msg = docker_service.pull_image(body.image_name or body.image or "")
        if msg.startswith("拉取失败"):
            return fail(msg)
        return ok({"msg": msg}, msg)

    if action == "delete_image":
        ref = body.id or body.image_name or body.image or ""
        msg = docker_service.delete_image(ref)
        if msg.startswith("删除失败"):
            return fail(msg)
        return ok({"msg": msg}, msg)

    if action == "test_docker":
        info = docker_service.docker_info()
        return ok(info, "连接成功" if info.get("connected") else "连接失败")

    if action == "save_setting":
        return ok(None, "配置已保存")

    return fail("未知操作")
