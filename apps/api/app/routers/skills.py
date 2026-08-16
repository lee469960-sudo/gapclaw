import json
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.deps import can_access_resource, get_session_user
from app.models import User, Skill
from app.schemas import ok, fail
from app.security import new_id, now_str

router = APIRouter(prefix="/pages/page_skills.cgi", tags=["skills"])


class SkillBody(BaseModel):
    action: str | None = None
    id: str | None = None
    name: str | None = None
    tags: str = ""
    description: str = ""
    visibility: str = "private"
    allowed_users: list[str] | None = None
    scope: str | None = None


def _filter_skills(items: list[Skill], user: User, scope: str = "all") -> list[Skill]:
    visible = [s for s in items if can_access_resource(user, s.visibility, s.allowed_users, s.creator)]
    if scope == "mine":
        return [s for s in visible if s.creator == user.username]
    return visible


def _get_skill_or_fail(db: Session, sid: str, user: User) -> Skill | None:
    s = db.query(Skill).filter(Skill.id == sid).first()
    if not s:
        return None
    if not can_access_resource(user, s.visibility, s.allowed_users, s.creator):
        return None
    return s


def _can_manage_skill(user: User, skill: Skill) -> bool:
    roles = json.loads(user.roles or "[]")
    if "master" in roles or "admin" in roles:
        return True
    return user.username == skill.creator


@router.get("")
async def skills_get(
    action: str = Query("list"),
    id: str = Query(None),
    scope: str = Query("all"),
    user: User = Depends(get_session_user),
    db: Session = Depends(get_db),
):
    if action == "list":
        items = _filter_skills(db.query(Skill).all(), user, scope)
        return ok([s.to_dict() for s in items])
    if action == "get" and id:
        s = _get_skill_or_fail(db, id, user)
        if not s:
            return fail("不存在")
        return ok(s.to_dict())
    if action == "view_md" and id:
        s = _get_skill_or_fail(db, id, user)
        if not s or not s.zip_path:
            return fail("无 SKILL.md")
        from app.services import skill_runtime
        content = skill_runtime.read_md(s) if s else _read_skill_md(s.zip_path)
        return ok({"content": content, "skill_name": s.name})
    if action == "read_md" and id:
        s = _get_skill_or_fail(db, id, user)
        if not s:
            return fail("不存在")
        from app.services import skill_runtime
        return ok({"content": skill_runtime.read_md(s)})
    if action == "read_script" and id:
        from app.services import skill_runtime
        s = _get_skill_or_fail(db, id, user)
        if not s:
            return fail("不存在")
        return ok({"content": skill_runtime.read_script(s, ""), "files": skill_runtime.list_scripts(s)})
    if action == "download" and id:
        from fastapi.responses import FileResponse
        s = _get_skill_or_fail(db, id, user)
        if not s or not s.zip_path:
            return fail("无文件")
        return FileResponse(s.zip_path, filename=s.zip_name or f"{s.name}.zip")
    return fail("未知操作")


@router.post("")
async def skills_post(
    request: Request,
    user: User = Depends(get_session_user),
    db: Session = Depends(get_db),
):
    content_type = request.headers.get("content-type", "")

    if "multipart/form-data" in content_type:
        form = await request.form()
        act = form.get("action") or "upload"
        if act == "upload" or form.get("file"):
            upload = form.get("file")
            if not upload or not hasattr(upload, "read"):
                return fail("请上传 Skill 压缩包")
            settings = get_settings()
            sid = form.get("id") or new_id()
            s = db.query(Skill).filter(Skill.id == sid).first()
            if not s:
                s = Skill(id=sid, creator=user.username)
                db.add(s)
            s.name = form.get("name") or s.name or "未命名"
            s.tags = form.get("tags") or ""
            s.description = form.get("description") or ""
            s.visibility = form.get("visibility") or "private"
            try:
                au = json.loads(form.get("allowed_users") or "[]")
            except Exception:
                au = []
            s.allowed_users = json.dumps(au if isinstance(au, list) else [])
            dest = Path(settings.upload_dir) / "skills" / f"{sid}.zip"
            dest.parent.mkdir(parents=True, exist_ok=True)
            data = await upload.read()
            with open(dest, "wb") as f:
                f.write(data)
            s.zip_path = str(dest)
            s.zip_name = getattr(upload, "filename", None) or f"{s.name}.zip"
            _extract_skill(dest, sid)
            s.modified_at = now_str()
            db.commit()
            return ok(s.to_dict(), "保存成功")
        return fail("未知操作")

    try:
        body = SkillBody(**await request.json())
    except Exception:
        return fail("请求格式错误")

    act = body.action or "list"

    if act == "update":
        if not body.id:
            return fail("缺少 id")
        s = db.query(Skill).filter(Skill.id == body.id).first()
        if not s:
            return fail("不存在")
        if not _can_manage_skill(user, s):
            return fail("无权限")
        if body.name is not None:
            s.name = body.name or s.name
        if body.tags is not None:
            s.tags = body.tags
        if body.description is not None:
            s.description = body.description
        if body.visibility is not None:
            s.visibility = body.visibility
        if body.allowed_users is not None:
            s.allowed_users = json.dumps(body.allowed_users)
        s.modified_at = now_str()
        db.commit()
        return ok(s.to_dict(), "保存成功")

    if act == "delete":
        if not body.id:
            return fail("缺少 id")
        s = db.query(Skill).filter(Skill.id == body.id).first()
        if not s:
            return fail("不存在")
        if not _can_manage_skill(user, s):
            return fail("无权限")
        if s.zip_path and Path(s.zip_path).exists():
            Path(s.zip_path).unlink(missing_ok=True)
        db.delete(s)
        db.commit()
        return ok(None, "删除成功")

    if act == "list":
        items = _filter_skills(db.query(Skill).all(), user, body.scope or "all")
        return ok([s.to_dict() for s in items])

    return fail("未知操作")


def _extract_skill(zip_path: Path, skill_id: str) -> None:
    settings = get_settings()
    dest_dir = Path(settings.data_dir) / "skills" / skill_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(dest_dir)


def _read_skill_md(zip_path: str) -> str:
    p = Path(zip_path)
    if p.suffix == ".zip":
        with zipfile.ZipFile(p, "r") as zf:
            for name in zf.namelist():
                if name.endswith("SKILL.md") or name == "SKILL.md":
                    return zf.read(name).decode("utf-8", errors="replace")
    skill_dir = Path(get_settings().data_dir) / "skills"
    for md in skill_dir.rglob("SKILL.md"):
        return md.read_text(encoding="utf-8", errors="replace")
    return "# SKILL.md not found"
