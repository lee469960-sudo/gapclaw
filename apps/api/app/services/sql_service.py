import re
from urllib.parse import urlparse, unquote

from app.security import decrypt_secret


def parse_database_url(url: str) -> dict | None:
    if not url or not url.startswith("postgresql"):
        return None
    parsed = urlparse(url)
    return {
        "db_type": "postgresql",
        "host": parsed.hostname or "127.0.0.1",
        "port": parsed.port or 5432,
        "username": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
        "database": (parsed.path or "/").lstrip("/") or "postgres",
    }


def _db_type(server) -> str:
    return (getattr(server, "db_type", None) or "mysql").lower()


def _password(server) -> str:
    return decrypt_secret(server.password_enc) if server.password_enc else ""


def _connect(server):
    db_type = _db_type(server)
    if db_type == "postgresql":
        import psycopg2
        return psycopg2.connect(
            host=server.host,
            port=server.port,
            user=server.username,
            password=_password(server),
            dbname=server.database or "postgres",
            connect_timeout=server.connect_timeout,
        )
    import pymysql
    return pymysql.connect(
        host=server.host,
        port=server.port,
        user=server.username,
        password=_password(server),
        database=server.database or None,
        connect_timeout=server.connect_timeout,
        read_timeout=server.read_timeout,
        write_timeout=getattr(server, "write_timeout", None) or server.read_timeout,
    )


def test_connection(server) -> dict:
    try:
        conn = _connect(server)
        cur = conn.cursor()
        if _db_type(server) == "postgresql":
            cur.execute("SELECT version()")
            version = cur.fetchone()[0]
        else:
            version = conn.get_server_info()
        conn.close()
        return {"connected": True, "version": version}
    except Exception as e:
        return {"connected": False, "error": str(e)}


def get_schema(server) -> dict:
    try:
        conn = _connect(server)
        cur = conn.cursor()
        if _db_type(server) == "postgresql":
            cur.execute("SELECT datname FROM pg_database WHERE datistemplate = false ORDER BY 1")
            dbs = [r[0] for r in cur.fetchall()]
            tables = []
            if server.database:
                cur.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY 1"
                )
                tables = [r[0] for r in cur.fetchall()]
        else:
            cur.execute("SHOW DATABASES")
            dbs = [r[0] for r in cur.fetchall()]
            tables = []
            if server.database:
                cur.execute(f"SHOW TABLES FROM `{server.database}`")
                tables = [r[0] for r in cur.fetchall()]
        conn.close()
        return {"databases": dbs, "tables": tables, "db_type": _db_type(server)}
    except Exception as e:
        return {"error": str(e)}


def _split_sql(sql: str) -> list[str]:
    parts = [p.strip() for p in re.split(r";\s*", sql.strip()) if p.strip()]
    return parts or [sql.strip()]


def execute_sql(server, sql: str, limit: int = 100) -> dict:
    statements = _split_sql(sql)
    last_result = {"column_names": [], "data": [], "affected_rows": 0, "truncated": False}
    try:
        conn = _connect(server)
        cur = conn.cursor()
        for stmt in statements:
            cur.execute(stmt)
            if cur.description:
                cols = [d[0] for d in cur.description]
                rows = cur.fetchmany(limit + 1)
                truncated = len(rows) > limit
                if truncated:
                    rows = rows[:limit]
                last_result = {
                    "column_names": cols,
                    "data": [list(r) for r in rows],
                    "affected_rows": 0,
                    "truncated": truncated,
                    "total_rows_hint": f">{limit}" if truncated else str(len(rows)),
                    "statement": stmt,
                }
            else:
                if _db_type(server) != "postgresql":
                    conn.commit()
                last_result = {
                    "column_names": [],
                    "data": [],
                    "affected_rows": cur.rowcount,
                    "truncated": False,
                    "statement": stmt,
                }
        if _db_type(server) == "postgresql":
            conn.commit()
        conn.close()
        return last_result
    except Exception as e:
        return {"column_names": [], "data": [], "error": str(e), "affected_rows": 0}
