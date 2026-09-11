from contextlib import asynccontextmanager
import logging
import time

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import get_settings
from app.database import SessionLocal, engine
from app.version import app_version
from app.middleware.page_auth import PagePermissionMiddleware
from app.schemas import fail
from app.startup import init_db
from app.routers import (
    auth, system_me, system_user, system_role,
    llm, skills, mcp, sandbox, agent, agent_chat, group, group_chat,
    sql, terminal, files, rag, httpmcp, site, monitor, docker_page, websockets,
    channel, channel_hooks, console_log, ops_alert, code_project, model_routing, release_callback, release_hook,
    release_management,
)

logger = logging.getLogger("app.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.ensure_dirs()
    if not settings.database_url.startswith("sqlite"):
        for attempt in range(30):
            try:
                with engine.connect() as conn:
                    conn.execute(text("SELECT 1"))
                break
            except Exception:
                if attempt >= 29:
                    raise
                time.sleep(2)
    db = SessionLocal()
    try:
        init_db(db)
    finally:
        db.close()
    from app.services.tick_scheduler import start_scheduler
    start_scheduler()
    from app.services.code_agent.lifecycle_janitor import start_lifecycle_janitor
    start_lifecycle_janitor()
    from app.services.channels.telegram import resync_all_telegram_channels
    await resync_all_telegram_channels(settings.public_base_url_normalized)
    from app.services.channels.telegram_poller import start_telegram_poller
    start_telegram_poller()
    yield
    from app.services.tick_scheduler import stop_scheduler
    stop_scheduler()
    from app.services.code_agent.lifecycle_janitor import stop_lifecycle_janitor
    stop_lifecycle_janitor()
    from app.services.channels.telegram_poller import stop_telegram_poller
    stop_telegram_poller()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="GAP API", version="1.9.0", lifespan=lifespan)

    app.add_middleware(PagePermissionMiddleware)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list + ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["ETag"],
    )

    routers = [
        auth, system_me, system_user, system_role,
        llm, skills, mcp, sandbox, agent, agent_chat, group, group_chat,
        sql, terminal, files, rag, httpmcp, site, monitor, docker_page, websockets,
        channel, channel_hooks, console_log, ops_alert, code_project, model_routing, release_callback, release_hook,
        release_management,
    ]
    for r in routers:
        app.include_router(r.router)

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        if isinstance(exc, StarletteHTTPException):
            return JSONResponse(
                status_code=exc.status_code,
                content={"detail": exc.detail},
            )
        if isinstance(exc, RequestValidationError):
            return JSONResponse(
                status_code=422,
                content={"detail": exc.errors()},
            )
        logger.exception(
            "unhandled exception path=%s method=%s",
            request.url.path,
            request.method,
        )
        return JSONResponse(
            status_code=500,
            content=fail("服务器内部错误", code=500),
        )

    static_dir = Path(__file__).parent.parent / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    site_upload_dir = Path(settings.data_dir).resolve() / "uploads" / "site"
    site_upload_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/statics/site", StaticFiles(directory=str(site_upload_dir)), name="statics_site")
    app.mount("/statics", StaticFiles(directory=str(static_dir)), name="statics")

    @app.get("/health")
    def health():
        return {"status": "ok", "version": app_version()}

    return app


app = create_app()
