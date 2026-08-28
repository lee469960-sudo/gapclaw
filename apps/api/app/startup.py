import json
import logging
from pathlib import Path
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import User, RoleDefinition
from app.security import hash_password, now_str


logger = logging.getLogger(__name__)


def init_db(db: Session) -> None:
    from app.database import Base, engine
    from app import models  # noqa: F401
    from sqlalchemy import inspect, text

    settings = get_settings()
    settings.ensure_dirs()
    readiness_check = getattr(settings, "code_agent_security_readiness", None)
    if callable(readiness_check):
        readiness = readiness_check()
        if not readiness["ready"]:
            logger.warning("CodeAgent admission disabled: %s", readiness["reason"])
    Base.metadata.create_all(bind=engine)
    from app.services.code_agent.schema_migration import upgrade_secure_workspace_schema
    upgrade_secure_workspace_schema(engine)

    insp = inspect(engine)
    if "users" in insp.get_table_names():
        ucols = {c["name"] for c in insp.get_columns("users")}
        if "organization_id" not in ucols:
            with engine.begin() as conn:
                conn.execute(text(
                    "ALTER TABLE users ADD COLUMN organization_id VARCHAR(64) DEFAULT 'default'"
                ))
    if "agents" in insp.get_table_names():
        cols = {c["name"] for c in insp.get_columns("agents")}
        if "profile" not in cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE agents ADD COLUMN profile VARCHAR(16) DEFAULT 'standard'"))
        if "code_project_id" not in cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE agents ADD COLUMN code_project_id VARCHAR(16) DEFAULT ''"))
        if "rags" not in cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE agents ADD COLUMN rags TEXT DEFAULT '[]'"))
        if "mcp_soft_circuit" not in cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE agents ADD COLUMN mcp_soft_circuit INTEGER DEFAULT 5"))
        if "tool_result_clip" not in cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE agents ADD COLUMN tool_result_clip INTEGER DEFAULT 6000"))
        if "httpmcps" not in cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE agents ADD COLUMN httpmcps TEXT DEFAULT '[]'"))
        if "engine" in cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE agents DROP COLUMN engine"))
    if "agent_groups" in insp.get_table_names():
        gcols = {c["name"] for c in insp.get_columns("agent_groups")}
        if "history_length" not in gcols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE agent_groups ADD COLUMN history_length INTEGER DEFAULT 10"))
    if "code_projects" in insp.get_table_names():
        cpcols = {c["name"] for c in insp.get_columns("code_projects")}
        with engine.begin() as conn:
            if "environment_tier" not in cpcols:
                conn.execute(text(
                    "ALTER TABLE code_projects ADD COLUMN environment_tier VARCHAR(32) "
                    "DEFAULT 'internal_non_production'"
                ))
            if "visibility" not in cpcols:
                conn.execute(text(
                    "ALTER TABLE code_projects ADD COLUMN visibility VARCHAR(16) DEFAULT 'private'"
                ))
            if "allowed_users" not in cpcols:
                conn.execute(text(
                    "ALTER TABLE code_projects ADD COLUMN allowed_users TEXT DEFAULT '[]'"
                ))
            if "organization_id" not in cpcols:
                conn.execute(text(
                    "ALTER TABLE code_projects ADD COLUMN organization_id VARCHAR(64) "
                    "DEFAULT 'default'"
                ))
            if "policy" not in cpcols:
                conn.execute(text(
                    "ALTER TABLE code_projects ADD COLUMN policy TEXT DEFAULT '{}'"
                ))
            if "workspace_retention_hours" not in cpcols:
                conn.execute(text(
                    "ALTER TABLE code_projects ADD COLUMN workspace_retention_hours INTEGER"
                ))
    if "code_project_manifests" in insp.get_table_names():
        cmcols = {c["name"] for c in insp.get_columns("code_project_manifests")}
        manifest_columns = {
            "source_id": "VARCHAR(16) DEFAULT ''",
            "source_type": "VARCHAR(16) DEFAULT ''",
            "credential_ref": "VARCHAR(128) DEFAULT ''",
            "requested_ref": "VARCHAR(128) DEFAULT ''",
            "resolved_commit": "VARCHAR(64) DEFAULT ''",
            "snapshot_id": "VARCHAR(16) DEFAULT ''",
            "snapshot_hash": "VARCHAR(64) DEFAULT ''",
            "source_scan_report_id": "VARCHAR(16) DEFAULT ''",
            "image_digest": "VARCHAR(128) DEFAULT ''",
            "security_schema_version": "INTEGER DEFAULT 0",
        }
        with engine.begin() as conn:
            for name, definition in manifest_columns.items():
                if name not in cmcols:
                    conn.execute(text(
                        f"ALTER TABLE code_project_manifests ADD COLUMN {name} {definition}"
                    ))
            conn.execute(text("""
                UPDATE code_project_manifests
                SET status = 'security_republish_required'
                WHERE status = 'published'
                  AND (
                    COALESCE(source_id, '') = ''
                    OR COALESCE(source_type, '') = ''
                    OR COALESCE(resolved_commit, '') = ''
                    OR COALESCE(snapshot_id, '') = ''
                    OR COALESCE(snapshot_hash, '') = ''
                    OR COALESCE(source_scan_report_id, '') = ''
                    OR COALESCE(image_digest, '') = ''
                    OR COALESCE(security_schema_version, 0) < 1
                  )
            """))
    if "code_source_snapshots" in insp.get_table_names():
        snapshot_columns = {
            column["name"] for column in insp.get_columns("code_source_snapshots")
        }
        cleanup_columns = {
            "cleanup_attempts": "INTEGER DEFAULT 0",
            "cleanup_error": "VARCHAR(64) DEFAULT ''",
            "cleanup_next_attempt": "VARCHAR(32) DEFAULT ''",
        }
        with engine.begin() as conn:
            for name, definition in cleanup_columns.items():
                if name not in snapshot_columns:
                    conn.execute(text(
                        f"ALTER TABLE code_source_snapshots ADD COLUMN {name} {definition}"
                    ))
    if "code_agent_runs" in insp.get_table_names():
        crcols = {c["name"] for c in insp.get_columns("code_agent_runs")}
        with engine.begin() as conn:
            if "session_id" not in crcols:
                conn.execute(text("ALTER TABLE code_agent_runs ADD COLUMN session_id VARCHAR(64) DEFAULT ''"))
            if "workspace_path" not in crcols:
                conn.execute(text("ALTER TABLE code_agent_runs ADD COLUMN workspace_path VARCHAR(1024) DEFAULT ''"))
            if "source_facts" not in crcols:
                conn.execute(text("ALTER TABLE code_agent_runs ADD COLUMN source_facts TEXT DEFAULT '{}'"))
            if "image" not in crcols:
                conn.execute(text("ALTER TABLE code_agent_runs ADD COLUMN image VARCHAR(255) DEFAULT ''"))
            if "runner_facts" not in crcols:
                conn.execute(text("ALTER TABLE code_agent_runs ADD COLUMN runner_facts TEXT DEFAULT '{}'"))
            if "container_id" not in crcols:
                conn.execute(text("ALTER TABLE code_agent_runs ADD COLUMN container_id VARCHAR(128) DEFAULT ''"))
            lifecycle_columns = {
                "runner_network_id": "VARCHAR(128) DEFAULT ''",
                "runner_state": "VARCHAR(32) DEFAULT 'not_started'",
                "execution_eligible": "BOOLEAN DEFAULT 0",
                "cleanup_state": "VARCHAR(32) DEFAULT 'not_required'",
                "cleanup_attempts": "INTEGER DEFAULT 0",
                "cleanup_error": "VARCHAR(64) DEFAULT ''",
                "cleanup_next_attempt": "VARCHAR(32) DEFAULT ''",
            }
            for name, definition in lifecycle_columns.items():
                if name not in crcols:
                    conn.execute(text(
                        f"ALTER TABLE code_agent_runs ADD COLUMN {name} {definition}"
                    ))
            if "workspace_state" not in crcols:
                conn.execute(text("ALTER TABLE code_agent_runs ADD COLUMN workspace_state VARCHAR(32) DEFAULT 'prepared'"))
            if "workspace_retention_hours" not in crcols:
                conn.execute(text(
                    "ALTER TABLE code_agent_runs ADD COLUMN workspace_retention_hours "
                    "INTEGER DEFAULT 168"
                ))
            if "retained_until" not in crcols:
                conn.execute(text("ALTER TABLE code_agent_runs ADD COLUMN retained_until VARCHAR(32) DEFAULT ''"))
            if "workspace_downloadable" not in crcols:
                conn.execute(text("ALTER TABLE code_agent_runs ADD COLUMN workspace_downloadable BOOLEAN DEFAULT 0"))
            if "tool_audit" not in crcols:
                conn.execute(text("ALTER TABLE code_agent_runs ADD COLUMN tool_audit TEXT DEFAULT '[]'"))
            if "tool_calls_used" not in crcols:
                conn.execute(text("ALTER TABLE code_agent_runs ADD COLUMN tool_calls_used INTEGER DEFAULT 0"))
            if "budget_usage" not in crcols:
                conn.execute(text("ALTER TABLE code_agent_runs ADD COLUMN budget_usage TEXT DEFAULT '{}'"))
            if "verification_baseline" not in crcols:
                conn.execute(text("ALTER TABLE code_agent_runs ADD COLUMN verification_baseline TEXT DEFAULT '{}'"))
            if "verifier_report" not in crcols:
                conn.execute(text("ALTER TABLE code_agent_runs ADD COLUMN verifier_report TEXT DEFAULT '{}'"))
            if "artifact_id" not in crcols:
                conn.execute(text("ALTER TABLE code_agent_runs ADD COLUMN artifact_id VARCHAR(16) DEFAULT ''"))
            run_columns = {
                "source_id": "VARCHAR(16) DEFAULT ''",
                "source_type": "VARCHAR(16) DEFAULT ''",
                "requested_ref": "VARCHAR(128) DEFAULT ''",
                "resolved_commit": "VARCHAR(64) DEFAULT ''",
                "snapshot_id": "VARCHAR(16) DEFAULT ''",
                "snapshot_hash": "VARCHAR(64) DEFAULT ''",
                "source_scan_report_id": "VARCHAR(16) DEFAULT ''",
                "image_digest": "VARCHAR(128) DEFAULT ''",
                "security_schema_version": "INTEGER DEFAULT 0",
                "effective_policy_hash": "VARCHAR(64) DEFAULT ''",
            }
            for name, definition in run_columns.items():
                if name not in crcols:
                    conn.execute(text(
                        f"ALTER TABLE code_agent_runs ADD COLUMN {name} {definition}"
                    ))
    if "sql_servers" in insp.get_table_names():
        scols = {c["name"] for c in insp.get_columns("sql_servers")}
        with engine.begin() as conn:
            if "db_type" not in scols:
                conn.execute(text("ALTER TABLE sql_servers ADD COLUMN db_type VARCHAR(32) DEFAULT 'mysql'"))
            if "remark" not in scols:
                conn.execute(text("ALTER TABLE sql_servers ADD COLUMN remark TEXT DEFAULT ''"))
            if "write_timeout" not in scols:
                conn.execute(text("ALTER TABLE sql_servers ADD COLUMN write_timeout INTEGER DEFAULT 60"))
    if "chat_summaries" in insp.get_table_names():
        scol = {c["name"] for c in insp.get_columns("chat_summaries")}
        if "chat_id" not in scol:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE chat_summaries ADD COLUMN chat_id VARCHAR(64) DEFAULT ''"))
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
                '"""ReAct engine stub - runtime executes via app.services.agent_runtime"""\n',
                encoding="utf-8",
            )

    from app.routers.system_role import BUILTIN_ROLES
    for item in BUILTIN_ROLES:
        rd = db.query(RoleDefinition).filter(RoleDefinition.name == item["name"]).first()
        if not rd:
            db.add(RoleDefinition(
                name=item["name"],
                label=item["label"],
                permissions=json.dumps(item["permissions"]),
                builtin=item["builtin"],
            ))
        elif rd.builtin:
            rd.label = item["label"]
            rd.permissions = json.dumps(item["permissions"])
    db.commit()

    from app.seed import run_seed
    try:
        run_seed(db, creator=settings.admin_username)
        from app.seed import seed_system_postgres
        seed_system_postgres(db, creator=settings.admin_username)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("seed skipped: %s", e)
