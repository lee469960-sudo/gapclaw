import json
from pathlib import Path
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import User, RoleDefinition
from app.security import hash_password, now_str


def init_db(db: Session) -> None:
    from app.database import Base, engine
    from app import models  # noqa: F401
    from sqlalchemy import inspect, text

    settings = get_settings()
    settings.ensure_dirs()
    Base.metadata.create_all(bind=engine)

    insp = inspect(engine)
    if "agents" in insp.get_table_names():
        cols = {c["name"] for c in insp.get_columns("agents")}
        if "rags" not in cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE agents ADD COLUMN rags TEXT DEFAULT '[]'"))
        if "mcp_soft_circuit" not in cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE agents ADD COLUMN mcp_soft_circuit INTEGER DEFAULT 5"))
    if "agent_groups" in insp.get_table_names():
        gcols = {c["name"] for c in insp.get_columns("agent_groups")}
        if "history_length" not in gcols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE agent_groups ADD COLUMN history_length INTEGER DEFAULT 10"))
    if "sql_servers" in insp.get_table_names():
        scols = {c["name"] for c in insp.get_columns("sql_servers")}
        with engine.begin() as conn:
            if "db_type" not in scols:
                conn.execute(text("ALTER TABLE sql_servers ADD COLUMN db_type VARCHAR(32) DEFAULT 'mysql'"))
            if "remark" not in scols:
                conn.execute(text("ALTER TABLE sql_servers ADD COLUMN remark TEXT DEFAULT ''"))
            if "write_timeout" not in scols:
                conn.execute(text("ALTER TABLE sql_servers ADD COLUMN write_timeout INTEGER DEFAULT 60"))
    if "im_channels" in insp.get_table_names():
        icols = {c["name"] for c in insp.get_columns("im_channels")}
        with engine.begin() as conn:
            if "visibility" not in icols:
                conn.execute(text("ALTER TABLE im_channels ADD COLUMN visibility VARCHAR(16) DEFAULT 'private'"))
            if "allowed_users" not in icols:
                conn.execute(text("ALTER TABLE im_channels ADD COLUMN allowed_users TEXT DEFAULT '[]'"))

    if "rag_corpus" in insp.get_table_names():
        rcols = {c["name"] for c in insp.get_columns("rag_corpus")}
        with engine.begin() as conn:
            if "index_status" not in rcols:
                conn.execute(text("ALTER TABLE rag_corpus ADD COLUMN index_status VARCHAR(16) DEFAULT 'pending'"))
            if "index_error" not in rcols:
                conn.execute(text("ALTER TABLE rag_corpus ADD COLUMN index_error TEXT DEFAULT ''"))
    if "rag_chunks" in insp.get_table_names():
        ccols = {c["name"] for c in insp.get_columns("rag_chunks")}
        with engine.begin() as conn:
            if "embedding_vec" not in ccols:
                conn.execute(text("ALTER TABLE rag_chunks ADD COLUMN embedding_vec TEXT DEFAULT ''"))
            # Ensure corpus_id index for candidate filtering
            try:
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_rag_chunks_corpus_id ON rag_chunks (corpus_id)"))
            except Exception:
                pass

    if "http_mcps" in insp.get_table_names():
        hcols = {c["name"] for c in insp.get_columns("http_mcps")}
        with engine.begin() as conn:
            if "version" not in hcols:
                conn.execute(text("ALTER TABLE http_mcps ADD COLUMN version VARCHAR(32) DEFAULT '1.0.0'"))
            if "auth_json" not in hcols:
                conn.execute(text("ALTER TABLE http_mcps ADD COLUMN auth_json TEXT DEFAULT '[]'"))
            if "tools_json" not in hcols:
                conn.execute(text("ALTER TABLE http_mcps ADD COLUMN tools_json TEXT DEFAULT '[]'"))

    if "mcps" in insp.get_table_names():
        mcols = {c["name"] for c in insp.get_columns("mcps")}
        with engine.begin() as conn:
            if "command" not in mcols:
                conn.execute(text("ALTER TABLE mcps ADD COLUMN command VARCHAR(255) DEFAULT ''"))
            if "command_args" not in mcols:
                conn.execute(text("ALTER TABLE mcps ADD COLUMN command_args TEXT DEFAULT '[]'"))
            if "command_env" not in mcols:
                conn.execute(text("ALTER TABLE mcps ADD COLUMN command_env TEXT DEFAULT '{}'"))

    # Optional pgvector for semantic search acceleration
    if engine.dialect.name == "postgresql":
        try:
            with engine.begin() as conn:
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        except Exception as e:
            import logging
            logging.getLogger(__name__).info("pgvector extension unavailable: %s", e)

    for sub in ["skills", "engine", "mcp", "llm", "files"]:
        Path(settings.data_dir, sub).mkdir(parents=True, exist_ok=True)

    admin = db.query(User).filter(User.username == settings.admin_username).first()
    if not admin:
        admin = User(
            username=settings.admin_username,
            password_hash=hash_password(settings.admin_password),
            roles=json.dumps(["master", "admin"]),
            permissions=json.dumps([]),
            assignable_roles=json.dumps(["master", "user"]),
        )
        db.add(admin)
        db.commit()

        engine_readme = Path(settings.data_dir) / "engine" / "react.py"
        if not engine_readme.exists():
            engine_readme.write_text(
                '"""ReAct engine stub - runtime executes via app.services.react_engine"""\n',
                encoding="utf-8",
            )

    if db.query(RoleDefinition).count() == 0:
        from app.routers.system_role import BUILTIN_ROLES
        for item in BUILTIN_ROLES:
            db.add(RoleDefinition(
                name=item["name"],
                label=item["label"],
                permissions=json.dumps(item["permissions"]),
                builtin=item["builtin"],
            ))
        db.commit()
    else:
        # Keep master/admin permissions in sync with MENU_GROUPS (e.g. new pages)
        from app.menu_config import ALL_PAGES
        for name in ("master", "admin"):
            rd = db.query(RoleDefinition).filter(RoleDefinition.name == name).first()
            if rd and rd.builtin:
                rd.permissions = json.dumps(ALL_PAGES)
        db.commit()

    from app.seed import run_seed
    try:
        run_seed(db, creator=settings.admin_username)
        from app.seed import seed_system_postgres
        seed_system_postgres(db, creator=settings.admin_username)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("seed skipped: %s", e)
