"""Idempotent default CodeAgent seed (project + published Manifest + code Agent)."""

from __future__ import annotations

import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.database import Base
from app.models import Agent, CodeProject, CodeProjectManifest, LLMResource
from app.seed import (
    CODE_AGENT_DEMO_AGENT,
    CODE_AGENT_DEMO_PROJECT,
    _seed_code_project,
)
import app.services.code_agent.manifest_publish as manifest_publish


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _ready_settings(tmp_path):
    data_root = tmp_path / "data"
    local_root = tmp_path / "repos"
    workspace_root = data_root / "workspaces"
    for directory in (local_root, workspace_root):
        directory.mkdir(parents=True)
    return Settings(
        data_dir=str(data_root),
        code_repository_allowlist="[]",
        code_repository_internal_cidrs="[]",
        code_local_repository_roots=json.dumps([str(local_root)]),
        code_trusted_image_digests=json.dumps([
            "registry.test/code-runner@sha256:" + "c" * 64,
        ]),
        code_workspace_api_root=str(workspace_root),
        code_workspace_host_root="/srv/code-workspaces",
    )


def _not_ready_settings(tmp_path):
    return Settings(
        data_dir=str(tmp_path / "data"),
        code_repository_allowlist="[]",
        code_repository_internal_cidrs="[]",
        code_local_repository_roots="[]",
        code_trusted_image_digests="[]",
        code_workspace_api_root="",
        code_workspace_host_root="",
    )


def test_seed_code_project_skips_when_security_config_not_ready(tmp_path):
    db = _db()
    out = _seed_code_project(db, "admin", _not_ready_settings(tmp_path), llm=None)
    assert out == {"code_project_id": None, "code_agent_id": None}
    assert db.query(CodeProject).count() == 0
    assert db.query(Agent).count() == 0


def test_seed_code_project_publishes_and_is_idempotent(tmp_path, monkeypatch):
    db = _db()
    llm = LLMResource(id="llm1", type="llm", name="MinMax", provider="openai")
    db.add(llm)
    db.commit()
    settings = _ready_settings(tmp_path)
    monkeypatch.setattr(
        manifest_publish, "get_settings", lambda: settings
    )

    first = _seed_code_project(db, "admin", settings, llm=llm)
    assert first["code_project_id"]
    assert first["code_agent_id"]

    project = (
        db.query(CodeProject)
        .filter(CodeProject.name == CODE_AGENT_DEMO_PROJECT)
        .first()
    )
    assert project is not None
    published = (
        db.query(CodeProjectManifest)
        .filter(
            CodeProjectManifest.project_id == project.id,
            CodeProjectManifest.status == "published",
        )
        .first()
    )
    assert published is not None
    assert published.snapshot_id
    assert published.snapshot_hash
    assert published.resolved_commit
    assert published.source_scan_report_id
    assert published.security_schema_version >= 1

    agent = db.query(Agent).filter(Agent.name == CODE_AGENT_DEMO_AGENT).first()
    assert agent is not None
    assert agent.profile == "code"
    assert agent.code_project_id == project.id

    # Second run must not duplicate the project, manifest or agent.
    project_count = db.query(CodeProject).count()
    agent_count = db.query(Agent).count()
    second = _seed_code_project(db, "admin", settings, llm=llm)
    assert second["code_project_id"] == project.id
    assert second["code_agent_id"] == agent.id
    assert db.query(CodeProject).count() == project_count
    assert db.query(Agent).count() == agent_count
