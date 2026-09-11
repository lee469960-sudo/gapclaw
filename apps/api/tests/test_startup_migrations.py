"""Compatibility coverage for startup migrations on existing SQLite databases."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

import app.database
import app.startup
from app.models import CodeProject, CodeProjectManifest, ModelRoleGroup, ModelRoutingPolicy, ModelRouteDecision
from app.routers.agent import agent_get


def test_code_project_manifest_status_allows_security_republish_required():
    assert CodeProjectManifest.__table__.c.status.type.length == 32


def test_existing_code_projects_table_is_migrated_for_agent_form_refs(monkeypatch, tmp_path):
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE code_projects (
                id VARCHAR(16) PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                description TEXT DEFAULT '',
                enabled BOOLEAN DEFAULT 1,
                environment_tier VARCHAR(32) DEFAULT 'internal_non_production',
                creator VARCHAR(64) DEFAULT '',
                created_at VARCHAR(32) DEFAULT '',
                modified_at VARCHAR(32) DEFAULT ''
            )
        """))

    monkeypatch.setattr(app.database, "engine", engine)
    monkeypatch.setattr(
        app.startup,
        "get_settings",
        lambda: SimpleNamespace(
            ensure_dirs=lambda: None,
            admin_username="admin",
            admin_password="password",
            data_dir=str(tmp_path / "data"),
        ),
    )
    db = sessionmaker(bind=engine)()
    try:
        app.startup.init_db(db)
        columns = {column["name"] for column in inspect(engine).get_columns("code_projects")}
        assert {
            "visibility", "allowed_users", "organization_id", "policy",
            "workspace_retention_hours",
        } <= columns

        db.add(CodeProject(id="project1", name="Project", creator="admin"))
        db.commit()
        response = asyncio.run(agent_get(
            action="refs",
            id=None,
            scope="all",
            user=SimpleNamespace(username="admin", roles="[]"),
            db=db,
        ))
        assert response["code"] == 0
        assert response["data"]["code_projects"] == [{
            "id": "project1",
            "name": "Project",
            "availability": {
                "ready": False,
                "status": "unavailable",
                "reason": "manifest_missing",
                "detail": "",
            },
        }]
    finally:
        db.close()


def test_existing_users_receive_default_organization_scope(monkeypatch, tmp_path):
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE users (
                id INTEGER PRIMARY KEY,
                username VARCHAR(64) NOT NULL UNIQUE,
                password_hash VARCHAR(255) NOT NULL,
                roles TEXT DEFAULT '["user"]',
                assignable_roles TEXT DEFAULT '[]',
                permissions TEXT DEFAULT '[]',
                disabled BOOLEAN DEFAULT 0,
                tokens TEXT DEFAULT '[]',
                created_at DATETIME
            )
        """))
        conn.execute(text("""
            INSERT INTO users (
                id, username, password_hash, roles, assignable_roles,
                permissions, disabled, tokens
            ) VALUES (1, 'admin', 'x', '["admin"]', '[]', '[]', 0, '[]')
        """))

    monkeypatch.setattr(app.database, "engine", engine)
    monkeypatch.setattr(
        app.startup,
        "get_settings",
        lambda: SimpleNamespace(
            ensure_dirs=lambda: None,
            admin_username="admin",
            admin_password="password",
            data_dir=str(tmp_path / "data"),
        ),
    )
    db = sessionmaker(bind=engine)()
    try:
        app.startup.init_db(db)
        columns = {column["name"] for column in inspect(engine).get_columns("users")}
        assert "organization_id" in columns
        assert db.execute(text(
            "SELECT organization_id FROM users WHERE username = 'admin'"
        )).scalar_one() == "default"
    finally:
        db.close()


def test_model_routing_schema_upgrade_preserves_legacy_llm_and_agent_bindings(monkeypatch, tmp_path):
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE llm_resources (
                id VARCHAR(16) PRIMARY KEY, type VARCHAR(16), name VARCHAR(255),
                members TEXT DEFAULT '[]'
            )
        """))
        conn.execute(text("""
            INSERT INTO llm_resources (id, type, name, members)
            VALUES ('group1', 'group', 'Legacy group', '[\"leaf1\", \"leaf2\"]')
        """))
        conn.execute(text("""
            CREATE TABLE agents (
                id VARCHAR(16) PRIMARY KEY, name VARCHAR(255), llm_id VARCHAR(16)
            )
        """))
        conn.execute(text("""
            INSERT INTO agents (id, name, llm_id) VALUES ('agent1', 'Legacy Agent', 'group1')
        """))

    monkeypatch.setattr(app.database, "engine", engine)
    monkeypatch.setattr(
        app.startup,
        "get_settings",
        lambda: SimpleNamespace(
            ensure_dirs=lambda: None,
            admin_username="admin", admin_password="password", data_dir=str(tmp_path / "data"),
        ),
    )
    db = sessionmaker(bind=engine)()
    try:
        app.startup.init_db(db)
        assert "routing_capabilities" in {
            column["name"] for column in inspect(engine).get_columns("llm_resources")
        }
        assert "routing_policy_id" in {
            column["name"] for column in inspect(engine).get_columns("agents")
        }
        assert db.execute(text("SELECT members FROM llm_resources WHERE id = 'group1'")).scalar_one() == '["leaf1", "leaf2"]'
        assert db.execute(text("SELECT llm_id FROM agents WHERE id = 'agent1'")).scalar_one() == "group1"
        assert {"model_role_groups", "model_routing_policies", "model_route_decisions"} <= set(inspect(engine).get_table_names())
        db.add_all([
            ModelRoleGroup(id="role1", name="General", role="general", preferred_llm_id="leaf1"),
            ModelRoutingPolicy(id="policy1", name="Default", router_llm_id="leaf1", role_group_ids='["role1"]'),
            ModelRouteDecision(id="route1", agent_id="agent1", session_id="session1", policy_id="policy1", policy_version=1, role="general", llm_id="leaf1"),
        ])
        db.commit()
        assert db.get(ModelRouteDecision, "route1").llm_id == "leaf1"
    finally:
        db.close()


def test_existing_manifest_and_run_tables_receive_frozen_contract_columns(monkeypatch, tmp_path):
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE code_project_manifests (
                id VARCHAR(16) PRIMARY KEY,
                project_id VARCHAR(16) NOT NULL,
                version INTEGER NOT NULL,
                status VARCHAR(16) DEFAULT 'draft'
            )
        """))
        conn.execute(text("""
            INSERT INTO code_project_manifests (id, project_id, version, status)
            VALUES ('legacy1', 'project1', 1, 'published')
        """))
        conn.execute(text("""
            CREATE TABLE code_agent_runs (
                id VARCHAR(16) PRIMARY KEY,
                agent_id VARCHAR(16) NOT NULL,
                project_id VARCHAR(16) NOT NULL,
                manifest_id VARCHAR(16) NOT NULL,
                manifest_version INTEGER NOT NULL
            )
        """))

    monkeypatch.setattr(app.database, "engine", engine)
    monkeypatch.setattr(
        app.startup,
        "get_settings",
        lambda: SimpleNamespace(
            ensure_dirs=lambda: None,
            admin_username="admin",
            admin_password="password",
            data_dir=str(tmp_path / "data"),
        ),
    )
    db = sessionmaker(bind=engine)()
    try:
        app.startup.init_db(db)
        manifest_columns = {
            column["name"] for column in inspect(engine).get_columns("code_project_manifests")
        }
        run_columns = {
            column["name"] for column in inspect(engine).get_columns("code_agent_runs")
        }
        assert {
            "source_id", "source_type", "credential_ref", "requested_ref",
            "resolved_commit", "snapshot_id", "snapshot_hash",
            "source_scan_report_id", "image_digest", "security_schema_version",
        } <= manifest_columns
        assert {
            "source_id", "source_type", "requested_ref", "resolved_commit",
            "snapshot_id", "snapshot_hash", "source_scan_report_id",
            "image_digest", "security_schema_version", "effective_policy_hash",
            "runner_network_id", "runner_state", "execution_eligible",
            "cleanup_state", "cleanup_attempts", "cleanup_error",
            "cleanup_next_attempt", "workspace_retention_hours",
        } <= run_columns
        migrated = db.execute(text(
            "SELECT status FROM code_project_manifests WHERE id = 'legacy1'"
        )).scalar_one()
        assert migrated == "security_republish_required"
    finally:
        db.close()


def test_existing_snapshot_table_receives_retryable_cleanup_columns(monkeypatch, tmp_path):
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE code_source_snapshots (
                id VARCHAR(16) PRIMARY KEY,
                status VARCHAR(16) DEFAULT 'sealed',
                ref_count INTEGER DEFAULT 0,
                cleanup_after VARCHAR(32) DEFAULT ''
            )
        """))

    monkeypatch.setattr(app.database, "engine", engine)
    monkeypatch.setattr(
        app.startup,
        "get_settings",
        lambda: SimpleNamespace(
            ensure_dirs=lambda: None,
            admin_username="admin",
            admin_password="password",
            data_dir=str(tmp_path / "data"),
        ),
    )
    db = sessionmaker(bind=engine)()
    try:
        app.startup.init_db(db)
        columns = {
            column["name"]
            for column in inspect(engine).get_columns("code_source_snapshots")
        }
        assert {
            "cleanup_attempts", "cleanup_error", "cleanup_next_attempt",
        } <= columns
    finally:
        db.close()
