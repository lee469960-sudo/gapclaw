import time
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_session_user
from app.models import User, SqlServer, SqlHistory
from app.schemas import ok, fail
from app.security import encrypt_secret, new_id, now_str
from app.services.sql_service import execute_sql, test_connection, get_schema

router = APIRouter(prefix="/pages/page_sql.cgi", tags=["sql"])


class SqlBody(BaseModel):
    action: str | None = None
    id: str | None = None
    name: str | None = None
    host: str = "127.0.0.1"
    port: int = 3306
    db_type: str = "mysql"
    username: str = "root"
    password: str = ""
    database: str = ""
    query_limit: int = 100
    connect_timeout: int = 10
    read_timeout: int = 60
    write_timeout: int = 60
    remark: str = ""
    sql: str | None = None
    limit: int = 100


@router.get("")
async def sql_get(
    action: str = Query("servers"),
    id: str = Query(None),
    user: User = Depends(get_session_user),
    db: Session = Depends(get_db),
):
    if action == "servers":
        return ok([s.to_dict() for s in db.query(SqlServer).order_by(SqlServer.name).all()])
    if action == "server_detail" and id:
        s = db.query(SqlServer).filter(SqlServer.id == id).first()
        return ok(s.to_dict(mask_pw=False) if s else None)
    if action == "history":
        rows = db.query(SqlHistory).filter(SqlHistory.username == user.username).order_by(SqlHistory.id.desc()).limit(100).all()
        return ok([
            {"id": r.id, "sql": r.sql_text, "server_id": r.server_id, "created_at": r.created_at}
            for r in rows
        ])
    if action == "schema" and id:
        s = db.query(SqlServer).filter(SqlServer.id == id).first()
        if not s:
            return fail("服务器不存在")
        return ok(get_schema(s))
    return fail("未知操作")


@router.post("")
async def sql_post(body: SqlBody, user: User = Depends(get_session_user), db: Session = Depends(get_db)):
    action = body.action or "save_server"

    if action == "save_server":
        if body.id:
            s = db.query(SqlServer).filter(SqlServer.id == body.id).first()
            if not s:
                return fail("不存在")
        else:
            s = SqlServer(id=new_id(), creator=user.username)
            db.add(s)
        s.name = body.name or s.name or "未命名"
        s.host = body.host
        s.port = body.port
        s.db_type = body.db_type or "mysql"
        s.username = body.username
        if body.password:
            s.password_enc = encrypt_secret(body.password)
        s.database = body.database
        s.query_limit = body.query_limit
        s.connect_timeout = body.connect_timeout
        s.read_timeout = body.read_timeout
        s.write_timeout = body.write_timeout
        s.remark = body.remark or ""
        db.commit()
        return ok(s.to_dict(), "保存成功")

    if action == "delete_server":
        db.query(SqlServer).filter(SqlServer.id == body.id).delete()
        db.commit()
        return ok(None, "删除成功")

    if action == "test":
        s = SqlServer(
            host=body.host,
            port=body.port,
            db_type=body.db_type or "mysql",
            username=body.username,
            password_enc=encrypt_secret(body.password),
            database=body.database,
            connect_timeout=body.connect_timeout,
            read_timeout=body.read_timeout,
            write_timeout=body.write_timeout,
        )
        result = test_connection(s)
        return ok(result) if result.get("connected") else fail(result.get("error", "连接失败"))

    if action == "schema":
        s = SqlServer(
            host=body.host,
            port=body.port,
            db_type=body.db_type or "mysql",
            username=body.username,
            password_enc=encrypt_secret(body.password),
            database=body.database,
            connect_timeout=body.connect_timeout,
            read_timeout=body.read_timeout,
            write_timeout=body.write_timeout,
        )
        if body.id:
            saved = db.query(SqlServer).filter(SqlServer.id == body.id).first()
            if saved:
                s = saved
        result = get_schema(s)
        return ok(result) if not result.get("error") else fail(result.get("error", "加载失败"))

    if action == "execute":
        s = db.query(SqlServer).filter(SqlServer.id == body.id).first()
        if not s:
            return fail("服务器不存在")
        t0 = time.time()
        result = execute_sql(s, body.sql or "", limit=body.limit or s.query_limit)
        result["time_cost"] = round((time.time() - t0) * 1000, 2)
        if not result.get("error"):
            db.add(SqlHistory(
                username=user.username,
                server_id=s.id,
                sql_text=body.sql or "",
                created_at=now_str(),
            ))
            db.commit()
        return ok(result) if not result.get("error") else fail(result.get("error", "执行失败"))

    if action == "clear_history":
        db.query(SqlHistory).filter(SqlHistory.username == user.username).delete()
        db.commit()
        return ok(None, "已清空")

    return fail("未知操作")
