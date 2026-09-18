import shutil
import time
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_session_user
from app.models import User
from app.schemas import ok, fail
from app.services.site_config import get_site_config, save_site_config, site_logo_path

router = APIRouter(prefix="/pages/page_site.cgi", tags=["site"])


class SiteBody(BaseModel):
    action: str | None = None
    site_name: str | None = None
    site_logo: str | None = None
    footer: str | None = None
    feature_flags: str | None = None
    version: str | None = None


def _site_logo_dest() -> Path:
    dest = site_logo_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    return dest


@router.get("")
async def site_get(user: User = Depends(get_session_user), db: Session = Depends(get_db)):
    return ok(get_site_config(db))


@router.post("")
async def site_post(request: Request, user: User = Depends(get_session_user), db: Session = Depends(get_db)):
    content_type = request.headers.get("content-type", "")

    if "multipart/form-data" in content_type:
        form = await request.form()
        act = form.get("action") or ""
        if act == "upload_logo":
            upload = form.get("file")
            if not upload or not hasattr(upload, "file"):
                return fail("请选择图片文件")
            dest = _site_logo_dest()
            with open(dest, "wb") as f:
                shutil.copyfileobj(upload.file, f)
            url = f"/statics/site/logo.png?v={int(time.time())}"
            save_site_config(db, {"site_logo": url})
            return ok({"site_logo": url}, "上传成功")
        return fail("未知操作")

    body = SiteBody(**await request.json())
    if body.action == "save":
        save_site_config(
            db,
            {
                "site_name": body.site_name if body.site_name is not None else "GAP",
                "site_logo": body.site_logo if body.site_logo is not None else "",
                "footer": body.footer if body.footer is not None else "",
                "feature_flags": body.feature_flags if body.feature_flags is not None else "{}",
                "version": body.version if body.version is not None else "",
            },
        )
        return ok(get_site_config(db), "保存成功")

    return fail("未知操作")
