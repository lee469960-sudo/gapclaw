"""CodeAgent project/Manifest control-plane API and UI contract coverage."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.database import Base
from app.main import app as api_app
import app.database
import app.routers.code_project
import app.services.code_agent.manifest_publish
import app.seed
import app.startup
from app.models import (
    Agent,
    CodeAgentRun,
    CodeControlAudit,
    CodeProject,
    CodeProjectManifest,
    CodeRepositorySource,
    CodeSourceSnapshot,
    LLMResource,
    RoleDefinition,
)
from app.menu_config import ALL_PAGES, PAGE_ROUTE_MAP
from app.routers.code_project import (
    CodeProjectBody,
    CodeProjectResponse,
    ManifestOptionsResponse,
    ManifestResponse,
    code_project_get,
    code_project_post,
)
from app.routers.agent import AgentBody, agent_get, agent_post
from app.routers.agent_chat import ChatBody, chat_get, chat_post
from app.routers.system_role import BUILTIN_ROLES
from app.security import encrypt_secret
import app.services.code_agent.control_plane as control_plane
from app.services.code_agent.control_plane import (
    ManifestUnavailableError,
    create_code_run,
    project_availability,
)
from app.services.code_agent.results import serialize_code_result
from app.services.code_agent.secret_store import DeployTokenSecretStore


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _user(username="admin", roles='["admin"]', permissions="[]"):
    return SimpleNamespace(username=username, roles=roles, permissions=permissions)


def _project(db, *, enabled=True, tier="internal_non_production"):
    project = CodeProject(
        id="project1",
        name="Project",
        enabled=enabled,
        environment_tier=tier,
        creator="admin",
    )
    db.add(project)
    db.commit()
    return project


def _security_settings(tmp_path):
    data_root = tmp_path / "data"
    workspace_root = data_root / "workspaces"
    workspace_root.mkdir(parents=True)
    return Settings(
        data_dir=str(data_root),
        code_repository_allowlist=json.dumps([
            "http://g.testskydata.com:80",
        ]),
        code_repository_internal_cidrs=json.dumps(["10.0.0.0/8"]),
        code_local_repository_roots="[]",
        code_trusted_image_digests=json.dumps([
            "registry.test/code-runner@sha256:" + "c" * 64,
        ]),
        code_workspace_api_root=str(workspace_root),
        code_workspace_host_root="/srv/code-workspaces",
    )


def _local_publish_settings(tmp_path, repository_root):
    data_root = tmp_path / "data"
    workspace_root = data_root / "workspaces"
    workspace_root.mkdir(parents=True)
    return Settings(
        data_dir=str(data_root),
        code_repository_allowlist="[]",
        code_repository_internal_cidrs="[]",
        code_local_repository_roots=json.dumps([str(repository_root)]),
        code_trusted_image_digests=json.dumps([
            "registry.test/code-runner@sha256:" + "c" * 64,
        ]),
        code_workspace_api_root=str(workspace_root),
        code_workspace_host_root="/srv/code-workspaces",
    )


def _git_repository(root, *, name="repository"):
    repository = root / name
    repository.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.name", "Test"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.email", "test@example.test"],
        check=True,
    )
    (repository / "model.sql").write_text("select 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "model.sql"], check=True)
    subprocess.run(["git", "-C", str(repository), "commit", "-qm", "initial"], check=True)
    return repository


def _save_local_draft(db, project, repository, admin):
    image_digest = "registry.test/code-runner@sha256:" + "c" * 64
    return asyncio.run(code_project_post(
        CodeProjectBody(
            action="save_draft",
            project_id=project.id,
            source_type="local",
            source_locator=str(repository),
            requested_ref="HEAD",
            image_digest=image_digest,
            allowed_paths=["model.sql"],
            validation_plan=[{"command": "pytest -q"}],
            allowed_tools=["read", "test"],
            policy={"network": False},
            budgets={"timeout_seconds": 60},
        ),
        admin,
        db,
    ))


def _manifest(db, *, allowed_paths='["src/"]'):
    manifest = CodeProjectManifest(
        id="manifest1",
        project_id="project1",
        version=1,
        status="published",
        source_id="source1",
        source_type="ssh",
        requested_ref="main",
        resolved_commit="a" * 40,
        snapshot_id="snapshot1",
        snapshot_hash="b" * 64,
        source_scan_report_id="scan1",
        repository="ssh://git.internal/example/repo.git",
        base_commit="a" * 40,
        allowed_paths=allowed_paths,
        validation_plan='[{"command": "pytest -q"}]',
        trusted_image="internal/python:3.12",
        image_digest="sha256:" + "c" * 64,
        security_schema_version=1,
        allowed_tools='["read", "test"]',
        policy='{"network": false}',
        budgets='{"timeout_seconds": 60}',
    )
    db.add(manifest)
    db.commit()
    return manifest


def _claude_manifest(db):
    manifest = _manifest(db, allowed_paths='["src/"]')
    manifest.policy = '{"network": false, "coding_runtime": "claude_code"}'
    db.commit()
    return manifest


def _ssh_ready(db, monkeypatch):
    """Seed a sealed snapshot and mock ready settings for the synthetic ssh Manifest."""
    db.add(CodeSourceSnapshot(
        id="snapshot1",
        source_id="source1",
        resolved_commit="a" * 40,
        content_hash="b" * 64,
        storage_path="/tmp/snapshot1",
        scan_report_id="scan1",
        importer_version="1",
        policy_hash="p" * 64,
        status="sealed",
    ))
    db.commit()

    class ReadySettings:
        code_repository_allowlist = '["ssh://git.internal:22"]'
        code_repository_internal_cidrs = "[]"
        code_local_repository_roots = "[]"
        code_trusted_image_digests = '["sha256:' + "c" * 64 + '"]'

        def code_agent_security_readiness(self):
            return {"ready": True, "status": "ready", "reason": "ready", "errors": {}}

    monkeypatch.setattr(control_plane, "get_settings", lambda: ReadySettings())


def _prepare_secure_draft(db, manifest_id: str, *, commit: str) -> CodeProjectManifest:
    manifest = db.get(CodeProjectManifest, manifest_id)
    manifest.source_id = "source1"
    manifest.source_type = "ssh"
    manifest.requested_ref = "main"
    manifest.resolved_commit = commit
    manifest.snapshot_id = "snapshot-" + manifest.id
    manifest.snapshot_hash = "b" * 64
    manifest.source_scan_report_id = "scan-" + manifest.id
    manifest.image_digest = "sha256:" + "c" * 64
    manifest.security_schema_version = 1
    db.commit()
    return manifest


def test_project_create_schema_and_response_contract():
    db = _db()
    response = asyncio.run(code_project_post(
        CodeProjectBody(
            action="create",
            name="payments-service",
            description="Internal service",
            environment_tier="internal_non_production",
            allowed_users=["developer"],
        ),
        _user(),
        db,
    ))

    assert response["code"] == 0
    ManifestOptionsResponse.model_validate(response["data"])
    payload = CodeProjectResponse.model_validate(response["data"])
    assert payload.name == "payments-service"
    assert payload.allowed_users == ["developer"]
    assert payload.availability.reason == "manifest_missing"
    assert db.query(CodeProject).count() == 1


def test_incomplete_manifest_request_and_response_schema_are_explicit():
    request = CodeProjectBody(action="save_draft", project_id="project1", repository="")
    response = ManifestResponse(
        id="manifest1",
        project_id=request.project_id,
        version=1,
        status="draft",
        validation_errors={"repository": "manifest_missing_repository"},
    )

    assert response.status == "draft"
    assert response.validation_errors == {"repository": "manifest_missing_repository"}
    assert response.allowed_paths == []


def test_missing_project_name_does_not_create_partial_row():
    db = _db()
    response = asyncio.run(code_project_post(
        CodeProjectBody(action="create", environment_tier="internal_non_production"),
        _user(),
        db,
    ))

    assert response == {"code": 1, "msg": "project_name_required", "data": None}
    assert db.query(CodeProject).count() == 0


def test_unsupported_environment_tier_does_not_create_partial_row():
    db = _db()
    response = asyncio.run(code_project_post(
        CodeProjectBody(action="create", name="production", environment_tier="production"),
        _user(),
        db,
    ))

    assert response == {"code": 1, "msg": "project_environment_not_allowed", "data": None}
    assert db.query(CodeProject).count() == 0


def test_user_without_page_permission_cannot_write_or_enumerate_projects():
    db = _db()
    outsider = _user("outsider", roles="[]")
    create_response = asyncio.run(code_project_post(
        CodeProjectBody(action="create", name="hidden"),
        outsider,
        db,
    ))
    list_response = asyncio.run(code_project_get("list", None, outsider, db))

    assert create_response["msg"] == "code_project_page_unauthorized"
    assert list_response["msg"] == "code_project_page_unauthorized"
    assert db.query(CodeProject).count() == 0


def test_code_project_page_route_menu_and_builtin_admin_permissions_are_registered():
    path = "/pages/page_code_project.cgi"
    roles = {item["name"]: item for item in BUILTIN_ROLES}

    assert PAGE_ROUTE_MAP[path] == "/code-projects"
    assert path in ALL_PAGES
    assert path in roles["master"]["permissions"]
    assert path in roles["admin"]["permissions"]
    assert path in roles["owner"]["permissions"]
    assert path in roles["operator"]["permissions"]
    assert path in roles["reviewer"]["permissions"]
    assert path not in roles["user"]["permissions"]
    assert path in {route.path for route in api_app.routes}


def test_startup_resynchronizes_existing_builtin_admin_roles(monkeypatch, tmp_path):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add_all([
        RoleDefinition(name="master", label="master", permissions="[]", builtin=True),
        RoleDefinition(name="admin", label="admin", permissions="[]", builtin=True),
    ])
    db.commit()
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
    monkeypatch.setattr(app.seed, "run_seed", lambda *args, **kwargs: None)
    monkeypatch.setattr(app.seed, "seed_system_postgres", lambda *args, **kwargs: None)

    app.startup.init_db(db)

    path = "/pages/page_code_project.cgi"
    for name in ("master", "admin", "owner", "operator", "reviewer"):
        role = db.query(RoleDefinition).filter(RoleDefinition.name == name).one()
        assert path in json.loads(role.permissions)
    db.close()


@pytest.mark.parametrize(
    ("enabled", "tier", "with_manifest", "allowed_paths", "reason"),
    [
        (False, "internal_non_production", False, '["src/"]', "project_disabled"),
        (True, "production", False, '["src/"]', "project_environment_not_allowed"),
        (True, "internal_non_production", False, '["src/"]', "manifest_missing"),
        (True, "internal_non_production", True, "[]", "manifest_invalid"),
    ],
)
def test_project_availability_has_stable_side_effect_free_states(
    enabled, tier, with_manifest, allowed_paths, reason
):
    db = _db()
    project = _project(db, enabled=enabled, tier=tier)
    if with_manifest:
        _manifest(db, allowed_paths=allowed_paths)

    availability = project_availability(db, project)

    assert availability["reason"] == reason
    assert availability["ready"] is False
    assert db.query(CodeAgentRun).count() == 0


def test_project_availability_is_ready_only_with_sealed_snapshot_and_settings(monkeypatch):
    db = _db()
    project = _project(db)
    _manifest(db, allowed_paths='["src/"]')
    _ssh_ready(db, monkeypatch)

    availability = project_availability(db, project)

    assert availability["reason"] == "ready"
    assert availability["ready"] is True
    assert availability["manifest_version"] == 1
    assert db.query(CodeAgentRun).count() == 0


def test_readiness_does_not_replace_fresh_run_admission_checks(monkeypatch):
    db = _db()
    project = _project(db)
    _manifest(db)
    _ssh_ready(db, monkeypatch)
    agent = Agent(id="agent1", name="Code Agent", profile="code", code_project_id=project.id)
    db.add(agent)
    db.commit()
    assert project_availability(db, project)["ready"] is True

    project.enabled = False
    db.commit()
    with pytest.raises(ManifestUnavailableError, match="project_unavailable"):
        create_code_run(
            db, agent=agent, actor=_user(), project_id=project.id, objective="Fix"
        )
    assert db.query(CodeAgentRun).count() == 0


def test_project_list_detail_update_and_enable_respect_page_and_project_acl():
    db = _db()
    page_permission = '["/pages/page_code_project.cgi"]'
    creator = _user("alice", roles='["owner"]', permissions=page_permission)
    allowed = _user("bob", roles='["operator"]', permissions=page_permission)
    outsider = _user("mallory", roles="[]", permissions=page_permission)
    created = asyncio.run(code_project_post(
        CodeProjectBody(action="create", name="Project", allowed_users=["bob"]),
        creator,
        db,
    ))
    project_id = created["data"]["id"]

    assert len(asyncio.run(code_project_get("list", None, creator, db))["data"]) == 1
    assert len(asyncio.run(code_project_get("list", None, allowed, db))["data"]) == 1
    assert asyncio.run(code_project_get("list", None, outsider, db))["data"] == []
    assert asyncio.run(code_project_get("get", project_id, outsider, db))["msg"] == "code_project_not_found"

    denied_role = asyncio.run(code_project_post(
        CodeProjectBody(action="update", id=project_id, description="Updated"),
        allowed,
        db,
    ))
    updated = asyncio.run(code_project_post(
        CodeProjectBody(action="update", id=project_id, description="Updated"),
        creator,
        db,
    ))
    denied = asyncio.run(code_project_post(
        CodeProjectBody(action="update", id=project_id, description="Leaked"),
        outsider,
        db,
    ))
    disabled = asyncio.run(code_project_post(
        CodeProjectBody(action="set_enabled", id=project_id, enabled=False),
        creator,
        db,
    ))

    assert denied_role["msg"] == "code_project_not_found"
    assert updated["data"]["description"] == "Updated"
    assert denied["msg"] == "code_project_not_found"
    assert disabled["data"]["enabled"] is False
    assert db.get(CodeProject, project_id).description == "Updated"


def test_incomplete_manifest_draft_is_saved_readable_and_not_runnable():
    db = _db()
    project = _project(db)
    admin = _user()
    saved = asyncio.run(code_project_post(
        CodeProjectBody(
            action="save_draft",
            project_id=project.id,
            repository="ssh://git.internal/example/repo.git",
        ),
        admin,
        db,
    ))
    loaded = asyncio.run(code_project_get("get_draft", project.id, admin, db))

    assert saved["code"] == 0
    assert saved["data"]["status"] == "draft"
    assert saved["data"]["validation_errors"]["base_commit"] == "manifest_missing_base_commit"
    assert loaded["data"] == saved["data"]
    assert db.query(CodeProjectManifest).count() == 1

    agent = Agent(id="agent1", name="Code Agent", profile="code", code_project_id=project.id)
    db.add(agent)
    db.commit()
    with pytest.raises(ManifestUnavailableError, match="manifest_missing"):
        create_code_run(
            db, agent=agent, actor=_user(), project_id=project.id, objective="Fix"
        )
    assert db.query(CodeAgentRun).count() == 0


def test_manifest_options_expose_only_approved_values_and_reference_metadata(
    monkeypatch,
    tmp_path,
):
    db = _db()
    project = _project(db)
    admin = _user()
    raw_secret = "deploy-token-must-never-leave-store"
    credential = DeployTokenSecretStore(db).assign(
        actor=admin,
        organization_id="default",
        label="GameStat read only",
        value=raw_secret,
        allowed_project_ids=[project.id],
        auth_username="git-reader",
        reference_id="credential1",
    )
    settings = _security_settings(tmp_path)
    monkeypatch.setattr(app.routers.code_project, "get_settings", lambda: settings)

    response = asyncio.run(code_project_get(
        "manifest_options",
        project.id,
        admin,
        db,
    ))
    serialized = json.dumps(response, sort_keys=True)

    assert response["code"] == 0
    assert response["data"]["source_types"] == ["http"]
    assert response["data"]["remote_origins"] == ["http://g.testskydata.com:80"]
    assert response["data"]["trusted_image_digests"] == [
        "registry.test/code-runner@sha256:" + "c" * 64,
    ]
    assert response["data"]["coding_runtimes"] == ["legacy", "claude_code"]
    assert response["data"]["credential_references"] == [credential.to_dict()]
    assert raw_secret not in serialized
    assert "secret_enc" not in serialized
    assert "ciphertext" not in serialized

    outsider = _user(
        "mallory",
        roles='["owner"]',
        permissions='["/pages/page_code_project.cgi"]',
    )
    denied = asyncio.run(code_project_get(
        "manifest_options",
        project.id,
        outsider,
        db,
    ))
    assert denied == {"code": 1, "msg": "code_project_not_found", "data": None}


def test_structured_manifest_draft_saves_canonical_approved_source_and_reference(
    monkeypatch,
    tmp_path,
):
    db = _db()
    project = _project(db)
    admin = _user()
    settings = _security_settings(tmp_path)
    monkeypatch.setattr(app.routers.code_project, "get_settings", lambda: settings)
    DeployTokenSecretStore(db).assign(
        actor=admin,
        organization_id="default",
        label="GameStat read only",
        value="private-value",
        allowed_project_ids=[project.id],
        reference_id="credential1",
    )
    image_digest = "registry.test/code-runner@sha256:" + "c" * 64

    saved = asyncio.run(code_project_post(
        CodeProjectBody(
            action="save_draft",
            project_id=project.id,
            source_type="http",
            source_locator="http://g.testskydata.com/system/dbt-gamestat-ck.git",
            credential_ref="credential1",
            requested_ref="main",
            image_digest=image_digest,
            allowed_paths=["models/"],
            validation_plan=[{"command": "pytest -q"}],
            allowed_tools=["read", "test"],
            coding_runtime="claude_code",
            policy={"network": False},
            budgets={"timeout_seconds": 60},
        ),
        admin,
        db,
    ))
    source = db.query(CodeRepositorySource).one()

    assert saved["code"] == 0
    assert saved["data"]["source_type"] == "http"
    assert saved["data"]["source_locator"] == (
        "http://g.testskydata.com:80/system/dbt-gamestat-ck.git"
    )
    assert saved["data"]["credential_ref"] == "credential1"
    assert saved["data"]["requested_ref"] == "main"
    assert saved["data"]["coding_runtime"] == "claude_code"
    assert saved["data"]["policy"]["coding_runtime"] == "claude_code"
    assert saved["data"]["trusted_image"] == "registry.test/code-runner"
    assert saved["data"]["image_digest"] == image_digest
    assert saved["data"]["security_validation"] == {
        "ready": True,
        "status": "ready_for_validation",
        "errors": {},
    }
    assert source.id == saved["data"]["source_id"]
    assert source.status == "draft"
    assert source.locator == saved["data"]["source_locator"]
    assert source.credential_ref == "credential1"
    assert source.requested_ref == "main"


def test_manifest_draft_can_create_and_bind_inline_git_credential_without_secret_leak(
    monkeypatch,
    tmp_path,
):
    db = _db()
    project = _project(db)
    admin = _user()
    settings = _security_settings(tmp_path)
    monkeypatch.setattr(app.routers.code_project, "get_settings", lambda: settings)
    image_digest = "registry.test/code-runner@sha256:" + "c" * 64
    raw_secret = "token-must-not-be-returned"

    saved = asyncio.run(code_project_post(
        CodeProjectBody(
            action="save_draft",
            project_id=project.id,
            source_type="http",
            source_locator="http://g.testskydata.com/system/dbt-gamestat-ck.git",
            credential_label="GameStat HTTP read only",
            credential_username="git-reader",
            credential_password=raw_secret,
            requested_ref="main",
            image_digest=image_digest,
            allowed_paths=["models/"],
            validation_plan=[{"command": "pytest -q"}],
            allowed_tools=["read", "test"],
            policy={"network": False},
            budgets={"timeout_seconds": 60},
        ),
        admin,
        db,
    ))
    serialized = json.dumps(saved, sort_keys=True)
    source = db.query(CodeRepositorySource).one()
    credentials = DeployTokenSecretStore(db).project_metadata(actor=admin, project=project)

    assert saved["code"] == 0
    assert saved["data"]["credential_ref"]
    assert source.credential_ref == saved["data"]["credential_ref"]
    assert len(credentials) == 1
    assert credentials[0].reference_id == saved["data"]["credential_ref"]
    assert credentials[0].auth_username == "git-reader"
    assert raw_secret not in serialized
    assert "secret_enc" not in serialized
    assert "ciphertext" not in serialized


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"source_type": "ftp"}, "source_type"),
        ({"credential_ref": "credential-outside-project"}, "credential_ref"),
        ({"requested_ref": "../main"}, "requested_ref"),
    ],
)
def test_unapproved_manifest_security_selections_are_rejected_atomically(
    monkeypatch,
    tmp_path,
    overrides,
    field,
):
    db = _db()
    project = _project(db)
    settings = _security_settings(tmp_path)
    monkeypatch.setattr(app.routers.code_project, "get_settings", lambda: settings)
    values = {
        "source_type": "http",
        "source_locator": "http://g.testskydata.com/system/dbt-gamestat-ck.git",
        "credential_ref": "",
        "requested_ref": "main",
        "image_digest": "registry.test/code-runner@sha256:" + "c" * 64,
    }
    values.update(overrides)

    rejected = asyncio.run(code_project_post(
        CodeProjectBody(action="save_draft", project_id=project.id, **values),
        _user(),
        db,
    ))

    assert rejected["msg"] == "manifest_security_selection_invalid"
    assert field in rejected["data"]["field_errors"]
    assert db.query(CodeProjectManifest).count() == 0
    assert db.query(CodeRepositorySource).count() == 0
    assert db.query(CodeControlAudit).count() == 0


def test_unapproved_image_digest_can_be_saved_as_repairable_draft(
    monkeypatch,
    tmp_path,
):
    db = _db()
    project = _project(db)
    settings = _security_settings(tmp_path)
    monkeypatch.setattr(app.routers.code_project, "get_settings", lambda: settings)
    image_digest = "registry.test/unapproved@sha256:" + "d" * 64

    saved = asyncio.run(code_project_post(
        CodeProjectBody(
            action="save_draft",
            project_id=project.id,
            source_type="http",
            source_locator="http://g.testskydata.com/system/dbt-gamestat-ck.git",
            requested_ref="main",
            image_digest=image_digest,
            allowed_paths=["models/"],
            validation_plan=[{"command": "pytest -q"}],
            allowed_tools=["read", "test"],
            policy={"network": False},
            budgets={"timeout_seconds": 60},
        ),
        _user(),
        db,
    ))

    assert saved["code"] == 0
    assert saved["data"]["image_digest"] == image_digest
    assert saved["data"]["validation_errors"]["image_digest"] == (
        "image_digest_not_allowed"
    )
    assert saved["data"]["security_validation"]["status"] == "draft_incomplete"
    assert db.query(CodeProjectManifest).count() == 1


def test_unapproved_remote_source_can_be_saved_as_repairable_draft(
    monkeypatch,
    tmp_path,
):
    db = _db()
    project = _project(db)
    settings = _security_settings(tmp_path)
    monkeypatch.setattr(app.routers.code_project, "get_settings", lambda: settings)
    image_digest = "registry.test/code-runner@sha256:" + "c" * 64

    saved = asyncio.run(code_project_post(
        CodeProjectBody(
            action="save_draft",
            project_id=project.id,
            source_type="http",
            source_locator="http://evil.test/repo.git",
            requested_ref="main",
            image_digest=image_digest,
            allowed_paths=["models/"],
            validation_plan=[{"command": "pytest -q"}],
            allowed_tools=["read", "test"],
            policy={"network": False},
            budgets={"timeout_seconds": 60},
        ),
        _user(),
        db,
    ))

    assert saved["code"] == 0
    assert saved["data"]["source_locator"] == "http://evil.test:80/repo.git"
    assert saved["data"]["validation_errors"]["source_locator"] == (
        "repository_source_not_allowed"
    )
    assert saved["data"]["security_validation"]["status"] == "draft_incomplete"
    assert db.query(CodeRepositorySource).count() == 1


def test_incomplete_structured_manifest_can_be_saved_as_repairable_draft(
    monkeypatch,
    tmp_path,
):
    db = _db()
    project = _project(db)
    settings = _security_settings(tmp_path)
    monkeypatch.setattr(app.routers.code_project, "get_settings", lambda: settings)

    saved = asyncio.run(code_project_post(
        CodeProjectBody(
            action="save_draft",
            project_id=project.id,
            source_type="",
            source_locator="",
            credential_ref="",
            requested_ref="",
            image_digest="",
        ),
        _user(),
        db,
    ))

    assert saved["code"] == 0
    assert saved["data"]["security_validation"]["status"] == "draft_incomplete"
    assert saved["data"]["validation_errors"]["source_type"] == (
        "manifest_missing_source_type"
    )
    assert saved["data"]["validation_errors"]["source_locator"] == (
        "manifest_missing_source_locator"
    )
    assert saved["data"]["validation_errors"]["requested_ref"] == (
        "manifest_missing_requested_ref"
    )
    assert saved["data"]["validation_errors"]["image_digest"] == (
        "manifest_missing_image_digest"
    )
    assert db.query(CodeRepositorySource).count() == 0


def test_manifest_publish_is_versioned_historical_and_published_rows_are_immutable(
    monkeypatch,
    tmp_path,
):
    db = _db()
    project = _project(db)
    _manifest(db)
    admin = _user()
    source_root = tmp_path / "sources"
    source_root.mkdir()
    repository = _git_repository(source_root)
    settings = _local_publish_settings(tmp_path, source_root)
    monkeypatch.setattr(
        app.services.code_agent.manifest_publish,
        "get_settings",
        lambda: settings,
    )
    monkeypatch.setattr(app.routers.code_project, "get_settings", lambda: settings)
    draft = _save_local_draft(db, project, repository, admin)
    published = asyncio.run(code_project_post(
        CodeProjectBody(
            action="publish",
            project_id=project.id,
            manifest_id=draft["data"]["id"],
        ),
        admin,
        db,
    ))
    history = asyncio.run(code_project_get("history", project.id, admin, db))
    immutable = asyncio.run(code_project_post(
        CodeProjectBody(
            action="save_draft",
            project_id=project.id,
            manifest_id=draft["data"]["id"],
            base_commit="c" * 40,
        ),
        admin,
        db,
    ))

    assert published["code"] == 0
    assert published["data"]["version"] == 2
    assert [row["version"] for row in history["data"]] == [2, 1]
    assert immutable["msg"] == "manifest_published_immutable"
    assert db.get(CodeProjectManifest, draft["data"]["id"]).base_commit == (
        published["data"]["resolved_commit"]
    )


def test_failed_manifest_publish_preserves_current_published_version():
    db = _db()
    project = _project(db)
    current = _manifest(db)
    admin = _user()
    draft = asyncio.run(code_project_post(
        CodeProjectBody(action="save_draft", project_id=project.id, repository="repo"),
        admin,
        db,
    ))

    failed = asyncio.run(code_project_post(
        CodeProjectBody(
            action="publish",
            project_id=project.id,
            manifest_id=draft["data"]["id"],
        ),
        admin,
        db,
    ))

    assert failed["msg"] == "manifest_invalid"
    assert db.get(CodeProjectManifest, current.id).status == "published"
    assert db.get(CodeProjectManifest, draft["data"]["id"]).status == "draft"
    assert project_availability(db, project)["manifest_version"] == 1


def test_manifest_publish_rejects_unsupported_project_environment_without_replacing_current():
    db = _db()
    project = _project(db, tier="production")
    current = _manifest(db)
    admin = _user()
    draft = asyncio.run(code_project_post(
        CodeProjectBody(
            action="save_draft",
            project_id=project.id,
            repository="ssh://git.internal/example/repo.git",
            base_commit="b" * 40,
            allowed_paths=["src/"],
            validation_plan=[{"command": "pytest -q"}],
            trusted_image="internal/python:3.12",
            allowed_tools=["read", "test"],
            policy={"network": False},
            budgets={"timeout_seconds": 60},
        ),
        admin,
        db,
    ))

    rejected = asyncio.run(code_project_post(
        CodeProjectBody(
            action="publish",
            project_id=project.id,
            manifest_id=draft["data"]["id"],
        ),
        admin,
        db,
    ))

    assert rejected["msg"] == "project_environment_not_allowed"
    assert rejected["data"]["field_errors"] == {
        "environment_tier": "project_environment_not_allowed",
    }
    assert db.get(CodeProjectManifest, current.id).status == "published"
    assert db.get(CodeProjectManifest, draft["data"]["id"]).status == "draft"


def test_manifest_publish_persistence_failure_rolls_back_atomically(monkeypatch, tmp_path):
    db = _db()
    project = _project(db)
    current = _manifest(db)
    admin = _user()
    source_root = tmp_path / "sources"
    source_root.mkdir()
    repository = _git_repository(source_root)
    settings = _local_publish_settings(tmp_path, source_root)
    monkeypatch.setattr(
        app.services.code_agent.manifest_publish,
        "get_settings",
        lambda: settings,
    )
    monkeypatch.setattr(app.routers.code_project, "get_settings", lambda: settings)
    draft = _save_local_draft(db, project, repository, admin)
    original_commit = db.commit
    commit_count = 0

    def fail_commit():
        nonlocal commit_count
        commit_count += 1
        if commit_count == 2:
            raise RuntimeError("storage unavailable")
        return original_commit()

    monkeypatch.setattr(db, "commit", fail_commit)
    failed = asyncio.run(code_project_post(
        CodeProjectBody(
            action="publish",
            project_id=project.id,
            manifest_id=draft["data"]["id"],
        ),
        admin,
        db,
    ))
    monkeypatch.setattr(db, "commit", original_commit)
    db.expire_all()

    assert failed["msg"] == "manifest_publish_failed"
    assert db.get(CodeProjectManifest, current.id).status == "published"
    assert db.get(CodeProjectManifest, draft["data"]["id"]).status == "draft"
    assert not db.get(CodeProjectManifest, draft["data"]["id"]).snapshot_id
    orphan = db.query(CodeSourceSnapshot).one()
    assert orphan.status == "failed"
    assert orphan.ref_count == 0
    assert project_availability(db, project)["manifest_version"] == 1


def test_control_plane_writes_are_audited_without_mutating_frozen_runs(monkeypatch):
    db = _db()
    project = _project(db)
    _manifest(db)
    _ssh_ready(db, monkeypatch)
    agent = Agent(id="agent1", name="Code Agent", profile="code", code_project_id=project.id)
    db.add(agent)
    db.commit()
    run = create_code_run(
        db, agent=agent, actor=_user(), project_id=project.id, objective="Fix"
    )
    frozen_contract = run.task_contract
    frozen_manifest_id = run.manifest_id
    db.commit()

    admin = _user()
    asyncio.run(code_project_post(
        CodeProjectBody(action="update", id=project.id, description="Changed"),
        admin,
        db,
    ))
    asyncio.run(code_project_post(
        CodeProjectBody(action="save_draft", project_id=project.id, repository="next"),
        admin,
        db,
    ))

    audits = db.query(CodeControlAudit).order_by(CodeControlAudit.created_at).all()
    assert [row.action for row in audits] == [
        "run_create",
        "project_update",
        "manifest_draft_save",
    ]
    assert all(row.actor == "admin" and row.project_id == project.id for row in audits)
    run_audit = audits[0]
    run_audit_details = json.loads(run_audit.details)
    assert run_audit.manifest_id == run.manifest_id
    assert run_audit_details["run_id"] == run.id
    assert run_audit_details["resolved_commit"] == run.resolved_commit
    assert run_audit_details["snapshot_id"] == run.snapshot_id
    assert run_audit_details["snapshot_hash"] == run.snapshot_hash
    assert run_audit_details["image_digest"] == run.image_digest
    assert run_audit_details["effective_policy_hash"] == run.effective_policy_hash
    db.refresh(run)
    assert run.task_contract == frozen_contract
    assert run.manifest_id == frozen_manifest_id


def test_code_project_component_is_wired_to_control_plane_states_and_actions():
    web_root = Path(__file__).resolve().parents[2] / "web" / "src"
    component = (web_root / "views" / "CodeProjects.vue").read_text(encoding="utf-8")
    router_source = (web_root / "router.js").read_text(encoding="utf-8")

    assert "'/pages/page_code_project.cgi'" in component
    for action in ("create", "update", "set_enabled", "get_draft", "history"):
        assert action in component
    assert "availabilityLabel" in component
    assert "errorMessage" in component
    assert "CodeProjects.vue" in router_source
    assert "'/code-projects'" in router_source


def test_manifest_component_groups_fields_and_disables_invalid_publish():
    component = (
        Path(__file__).resolve().parents[2] / "web" / "src" / "views" / "CodeProjects.vue"
    ).read_text(encoding="utf-8")

    for heading in ("仓库与基线", "路径与验证", "执行镜像与工具", "策略与预算"):
        assert heading in component
    assert "fieldError('source_locator')" in component
    assert "fieldError('image_digest')" in component
    assert "fieldError('budgets')" in component
    assert ':disabled="!draft || hasValidationErrors"' in component
    assert "saveDraft" in component


def test_manifest_component_limits_security_fields_to_server_options_and_references():
    component = (
        Path(__file__).resolve().parents[2] / "web" / "src" / "views" / "CodeProjects.vue"
    ).read_text(encoding="utf-8")

    assert "action: 'manifest_options'" in component
    assert 'v-model="manifestForm.source_type"' in component
    assert "manifestOptions.source_types" in component
    assert 'v-model="manifestForm.coding_runtime"' in component
    assert "codingRuntimeOptions" in component
    assert "policy.coding_runtime = manifestForm.coding_runtime || 'legacy'" in component
    assert "coding_runtime: manifestForm.coding_runtime || 'legacy'" in component
    assert 'v-model="manifestForm.credential_ref"' in component
    assert 'v-model="manifestForm.credential_label"' in component
    assert 'v-model="manifestForm.credential_username"' in component
    assert 'v-model="manifestForm.credential_password"' in component
    assert "manifestOptions.credential_references" in component
    assert "credential.reference_id" in component
    assert "show-password" in component
    assert "不会回显" in component
    assert 'v-model="manifestForm.requested_ref"' in component
    assert 'v-model="manifestForm.image_digest"' in component
    assert "manifestOptions.trusted_image_digests" in component
    assert "可信镜像由环境变量管理" in component
    assert "multi-arch manifest-list digest" in component
    assert "draft?.security_validation" in component
    assert "secret_enc" not in component


def test_manifest_component_confirms_publish_and_renders_read_only_history():
    component = (
        Path(__file__).resolve().parents[2] / "web" / "src" / "views" / "CodeProjects.vue"
    ).read_text(encoding="utf-8")

    assert "发布后该版本不可修改" in component
    assert 'action: \'publish\'' in component
    assert 'action: \'history\'' in component
    assert "JSON.stringify(item, null, 2)" in component
    assert "await loadProjects()" in component


def test_agent_chat_renders_code_profile_events_as_visible_steps():
    component = (
        Path(__file__).resolve().parents[2] / "web" / "src" / "views" / "AgentChat.vue"
    ).read_text(encoding="utf-8")

    assert "data.type === 'profile'" in component
    assert "profileEventToStep" in component
    assert "CodeAgent 运行阶段" in component
    assert "runtime_result: 'Claude Code Runtime 结果'" in component
    assert "codeRuntimeHistorySteps" not in component
    assert "class=\"exec-card\"" in component
    assert "startsWith('code_')" in component


def test_existing_llm_ui_exposes_anthropic_claude_configuration_for_codeagent():
    component = (Path(__file__).resolve().parents[2] / "web" / "src" / "views" / "Llms.vue").read_text(encoding="utf-8")
    assert "applyAnthropicPreset" in component
    assert "form.provider = 'anthropic'" in component
    assert "form.base_url = 'https://api.anthropic.com'" in component
    assert "Claude Code 使用 Anthropic API" in component


def test_agent_chat_exposes_run_bound_code_workspace_metadata():
    router = (Path(__file__).resolve().parents[1] / "app" / "routers" / "agent_chat.py").read_text(encoding="utf-8")
    assert 'action == "get_code_workspace"' in router
    assert 'workspace_not_prepared' in router
    assert 'workspace_expired' in router
    assert 'workspace_mount_invalid' in router
    assert 'workspace_path' in router
    assert '"/workplace"' not in router[router.index('action == "get_code_workspace"'):router.index('if action in {"review_code_artifact"')]


def test_agent_chat_exposes_redacted_code_runtime_events():
    router = (Path(__file__).resolve().parents[1] / "app" / "routers" / "agent_chat.py").read_text(encoding="utf-8")
    assert 'action == "get_code_events"' in router
    assert "claude_code_runtime_history" in router
    assert "has_more" in router
    assert "redact_code_output" in router
    assert "next_since" in router
    assert "since: int = Query(0)" in router


def test_agent_chat_uses_code_workspace_panel_only_for_code_profile():
    component = (Path(__file__).resolve().parents[2] / "web" / "src" / "views" / "AgentChat.vue").read_text(encoding="utf-8")
    assert "CodeWorkspacePanel" in component
    assert 'agent?.profile === "code"' in component or "agent?.profile === 'code'" in component
    assert '<CodeWorkspacePanel' in component
    assert 'v-if="agent?.profile === \'code\'"' in component
    assert ':agent-id="agentId"' in component
    assert "code-run-id=\"activeCodeRunId\"" in component
    assert 'ref="codeWpRef"' in component
    assert "function reloadWorkspacePanels()" in component
    assert "WorkplacePanel v-else" in component


def test_agent_chat_keeps_last_workspace_when_clarification_has_no_new_run():
    component = (Path(__file__).resolve().parents[2] / "web" / "src" / "views" / "AgentChat.vue").read_text(encoding="utf-8")
    assert "async function hydrateActiveCodeRun()" in component
    assert "const nextCodeRunId = String(submitted.data?.code_run_id || '').trim()" in component
    assert "if (nextCodeRunId)" in component
    assert "await hydrateActiveCodeRun()" in component
    assert "activeCodeRunId.value = ''" in component
    assert 'class="workspace-panel-empty">等待 CodeAgent Run</div>' in (
        Path(__file__).resolve().parents[2] / "web" / "src" / "components" / "CodeWorkspacePanel.vue"
    ).read_text(encoding="utf-8")


def test_code_workspace_panel_sends_agent_id_with_every_run_bound_request():
    component = (Path(__file__).resolve().parents[2] / "web" / "src" / "components" / "CodeWorkspacePanel.vue").read_text(encoding="utf-8")
    assert "agentId: { type: String, default: '' }" in component
    assert "agent_id: props.agentId" in component
    assert "agent_id: props.agentId, code_run_id: props.codeRunId" in component


def test_code_workspace_panel_renders_distinct_preview_states():
    component = (Path(__file__).resolve().parents[2] / "web" / "src" / "components" / "CodeWorkspacePanel.vue").read_text(encoding="utf-8")
    assert "Workspace 文件树为空" in component
    assert "Workspace 已过期或已清理" in component
    assert "Workspace 挂载无效" in component
    assert "Workspace 正在准备，等待仓库挂载" in component
    assert "workspace_unsupported" in component
    assert "请升级服务端或查看对话结果" in component
    assert "meta.msg || meta.data?.state" not in component
    assert "git_metadata_missing" in component
    assert "Git metadata unavailable" in component
    assert "Git {{ git.branch" in component
    assert "workspaceErrorLabel" in component
    assert "visibleEntries" in component
    assert "toggleDirectory" in component
    assert "workspace-entry-changed" in component
    assert "已修改" in component


def test_retained_workspace_without_git_keeps_file_preview_and_reports_git_metadata_separately(tmp_path):
    db = _db()
    project = _project(db)
    db.add(Agent(id="code-retained", name="Code", profile="code", code_project_id=project.id))
    workspace = tmp_path / "run" / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "model.sql").write_text("select 1\n", encoding="utf-8")
    run = CodeAgentRun(
        id="run-retained", agent_id="code-retained", session_id="session-retained",
        project_id=project.id, manifest_id="manifest-retained", manifest_version=1,
        workspace_path=str(workspace), workspace_state="retained_read_only",
    )
    db.add(run)
    db.commit()
    user = _user()
    common = dict(
        request=SimpleNamespace(headers={}), agent_id="code-retained", session_id="session-retained",
        path="", code_run_id="run-retained", artifact_id=None, artifact_kind=None,
        message_id=None, limit=50, since=0, user=user, db=db,
    )

    tree = asyncio.run(chat_get(action="list_code_workspace", **common))
    git = asyncio.run(chat_get(action="get_code_workspace_git", **common))

    assert tree["data"]["entries"][0]["path"] == "model.sql"
    assert git["data"] == {
        "available": False, "reason": "git_metadata_missing", "branch": "",
        "changed_files": [], "clean": None,
    }


def test_legacy_agent_chat_workspace_flow_remains_the_standard_agent_fallback():
    component = (Path(__file__).resolve().parents[2] / "web" / "src" / "views" / "AgentChat.vue").read_text(encoding="utf-8")
    router = (Path(__file__).resolve().parents[1] / "app" / "routers" / "agent_chat.py").read_text(encoding="utf-8")
    assert "WorkplacePanel v-else" in component
    assert "agentSandboxId" in component
    assert 'action == "list_workplace"' in router


def test_agent_chat_rehydrates_and_deduplicates_code_runtime_events():
    component = (Path(__file__).resolve().parents[2] / "web" / "src" / "views" / "AgentChat.vue").read_text(encoding="utf-8")
    assert "seenCodeEventKeys" in component
    assert "loadCodeEvents" in component
    assert "get_code_events" in component
    assert "applyCodeRuntimeEvent" in component
    assert "findOpenCodeStepIndex" in component
    assert "op: openIndex == null ? 'append' : 'patch'" in component
    assert "applyCodeRuntimeEvent(event)" in component
    assert "historyOnly: true" not in component
    assert "profile.phase === 'runtime_started' && rawStatus === 'started'" in component


def test_agent_chat_auto_reopens_latest_code_execution_after_refresh():
    component = (Path(__file__).resolve().parents[2] / "web" / "src" / "views" / "AgentChat.vue").read_text(encoding="utf-8")
    assert "async function hydrateLatestCodeHistory()" in component
    assert "hydrateLatestCodeHistory().catch(() => {})" in component
    assert "execOpen.value = { ...execOpen.value, [latest.execKey]: true }" in component
    assert "await ensureHistorySteps(latest.execKey)" in component


def test_claude_code_runtime_emits_terminal_done_event_after_cleanup():
    runtime = (Path(__file__).resolve().parents[1] / "app" / "services" / "agent_runtime" / "runtime.py").read_text(encoding="utf-8")
    assert "publish_code_done = bool(run and code_run_uses_claude_code(run))" in runtime
    assert '"type": "done"' in runtime[runtime.index("publish_code_done = bool"):]
    assert "_code_profile_steps_for_message(run)" in runtime


def test_agent_chat_renders_code_result_in_chat_markdown_without_standalone_card():
    component = (Path(__file__).resolve().parents[2] / "web" / "src" / "views" / "AgentChat.vue").read_text(encoding="utf-8")
    assert "renderMessage(item.m)" in component
    assert "code-result-banner" not in component
    assert "codeRunResult" not in component
    assert "codeArtifactReview" not in component


def test_agent_chat_shows_code_stage_detail_inline():
    component = (Path(__file__).resolve().parents[2] / "web" / "src" / "views" / "AgentChat.vue").read_text(encoding="utf-8")
    assert "stepInlineDetail(step)" in component
    assert "step-inline-detail" in component
    assert "Claude Code Runtime 结果" in component


def test_code_workspace_and_events_endpoints_require_run_bound_reviewer():
    router = (Path(__file__).resolve().parents[1] / "app" / "routers" / "agent_chat.py").read_text(encoding="utf-8")
    for action in ("get_code_workspace", "get_code_events", "list_code_workspace", "read_code_workspace_file", "get_code_workspace_git"):
        start = router.index(f'action == "{action}"') if action in {"get_code_workspace", "get_code_events"} else router.index(f'"{action}"')
        assert "CodeAgentRun" in router[start:start + 1000]
        assert "require_project_reviewer" in router[start:start + 1400]
    assert "Path(run.workspace_path).resolve(strict=True)" in router


def test_concurrent_standard_and_code_sessions_keep_workspace_and_event_streams_isolated(tmp_path):
    db = _db()
    project = _project(db)
    db.add_all([
        Agent(id="code-concurrent", name="Code", profile="code", code_project_id=project.id),
        Agent(id="standard-concurrent", name="Standard", profile="standard"),
    ])
    workspace_a = tmp_path / "run-a" / "workspace"
    workspace_b = tmp_path / "run-b" / "workspace"
    for workspace, filename, content in (
        (workspace_a, "a.sql", "select 'a'\n"),
        (workspace_b, "b.sql", "select 'b'\n"),
    ):
        (workspace / ".git").mkdir(parents=True)
        (workspace / filename).write_text(content, encoding="utf-8")
    db.add_all([
        CodeAgentRun(
            id="run-a", agent_id="code-concurrent", session_id="code-session-a",
            project_id=project.id, manifest_id="manifest-a", manifest_version=1,
            workspace_path=str(workspace_a), workspace_state="prepared",
            runner_facts=json.dumps({"claude_code_runtime_history": [{"phase": "read", "summary": "a"}]}),
        ),
        CodeAgentRun(
            id="run-b", agent_id="code-concurrent", session_id="code-session-b",
            project_id=project.id, manifest_id="manifest-b", manifest_version=1,
            workspace_path=str(workspace_b), workspace_state="prepared",
            runner_facts=json.dumps({"claude_code_runtime_history": [{"phase": "test", "summary": "b"}]}),
        ),
    ])
    db.commit()
    user = _user()

    events_a = asyncio.run(chat_get(
        None, action="get_code_events", agent_id="code-concurrent", code_run_id="run-a",
        limit=50, since=0, user=user, db=db,
    ))
    events_b = asyncio.run(chat_get(
        None, action="get_code_events", agent_id="code-concurrent", code_run_id="run-b",
        limit=50, since=0, user=user, db=db,
    ))
    tree_a = asyncio.run(chat_get(
        None, action="list_code_workspace", agent_id="code-concurrent", code_run_id="run-a",
        path="", limit=500, user=user, db=db,
    ))
    tree_b = asyncio.run(chat_get(
        None, action="list_code_workspace", agent_id="code-concurrent", code_run_id="run-b",
        path="", limit=500, user=user, db=db,
    ))

    assert events_a["data"]["run_id"] == "run-a"
    assert [event["summary"] for event in events_a["data"]["events"]] == ["a"]
    assert events_b["data"]["run_id"] == "run-b"
    assert [event["summary"] for event in events_b["data"]["events"]] == ["b"]
    assert {entry["path"] for entry in tree_a["data"]["entries"]} == {"a.sql"}
    assert {entry["path"] for entry in tree_b["data"]["entries"]} == {"b.sql"}

    component = (Path(__file__).resolve().parents[2] / "web" / "src" / "views" / "AgentChat.vue").read_text(encoding="utf-8")
    assert '<CodeWorkspacePanel' in component
    assert 'v-if="agent?.profile === \'code\'"' in component
    assert "WorkplacePanel v-else" in component
    assert "if (agent.value?.profile !== 'code' || !activeCodeRunId.value) return" in component


def test_code_agent_workspace_conversation_mock_acceptance_covers_preview_progress_and_terminal_results(tmp_path):
    db = _db()
    project = _project(db)
    db.add(Agent(id="code-acceptance", name="Code", profile="code", code_project_id=project.id))
    workspace = tmp_path / "run" / "workspace"
    workspace.mkdir(parents=True)
    subprocess.run(["git", "-C", str(workspace), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(workspace), "config", "user.name", "Acceptance"], check=True)
    subprocess.run(["git", "-C", str(workspace), "config", "user.email", "acceptance@example.test"], check=True)
    (workspace / "models").mkdir()
    (workspace / "models" / "model.sql").write_text("select 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(workspace), "add", "models/model.sql"], check=True)
    subprocess.run(["git", "-C", str(workspace), "commit", "-qm", "base"], check=True)
    run = CodeAgentRun(
        id="run-acceptance", agent_id="code-acceptance", session_id="session-acceptance",
        project_id=project.id, manifest_id="manifest-acceptance", manifest_version=1,
        repository="http://git.example.test/repo.git", base_commit="a" * 40,
        resolved_commit="b" * 40, workspace_path=str(workspace), workspace_state="prepared",
        runner_facts=json.dumps({"claude_code_runtime_history": [
            {"phase": "workspace", "status": "completed", "summary": "Workspace ready"},
            {"phase": "test", "status": "completed", "summary": "Tests passed"},
        ]}),
        status="patch_ready", verifier_report=json.dumps({"passed": True}),
    )
    db.add(run)
    db.commit()
    user = _user()
    common = dict(
        request=SimpleNamespace(headers={}), agent_id="code-acceptance",
        session_id="session-acceptance", path="", code_run_id="run-acceptance",
        artifact_id=None, artifact_kind=None, message_id=None, limit=50, since=0,
        user=user, db=db,
    )

    metadata = asyncio.run(chat_get(action="get_code_workspace", **common))
    tree = asyncio.run(chat_get(action="list_code_workspace", **common))
    progress = asyncio.run(chat_get(action="get_code_events", **common))
    git = asyncio.run(chat_get(action="get_code_workspace_git", **common))
    ready = serialize_code_result(run, SimpleNamespace(
        id="artifact-acceptance", status="sealed", base_commit="a" * 40,
        diff_hash="c" * 64, policy_hash="d" * 64, verifier_report_hash="e" * 64,
        image="internal/code", image_id="sha256:image",
    ))
    blocked_run = SimpleNamespace(
        id="run-acceptance", project_id=project.id, status="target_not_found",
        failure_reason="target_not_found", manifest_id="manifest-acceptance",
        manifest_version=1, verifier_report=json.dumps({"passed": False}),
        budget_usage="{}", task_contract="{}", effective_policy="{}", runner_facts="{}",
    )
    blocked = serialize_code_result(blocked_run)

    assert metadata["data"]["state"] == "ready"
    assert metadata["data"]["repository"].endswith("repo.git")
    assert {entry["path"] for entry in tree["data"]["entries"]} == {"models"}
    assert [event["phase"] for event in progress["data"]["events"]] == ["workspace", "test"]
    assert git["data"]["clean"] is True
    assert ready["status"] == "patch_ready" and ready["directly_adoptable"] is True
    assert blocked["status"] == "target_not_found" and blocked["directly_adoptable"] is False


def test_sealed_run_without_retained_workspace_reports_expired_not_mount_invalid(tmp_path):
    db = _db()
    project = _project(db)
    db.add(Agent(id="code-sealed-missing", name="Code", profile="code", code_project_id=project.id))
    run = CodeAgentRun(
        id="run-sealed-missing", agent_id="code-sealed-missing", session_id="session-sealed-missing",
        project_id=project.id, manifest_id="manifest-sealed-missing", manifest_version=1,
        workspace_path=str(tmp_path / "gone" / "workspace"), workspace_state="sealed", status="patch_ready",
    )
    db.add(run)
    db.commit()

    result = asyncio.run(chat_get(
        action="get_code_workspace", request=SimpleNamespace(headers={}),
        agent_id="code-sealed-missing", session_id="session-sealed-missing",
        path="", code_run_id=run.id, artifact_id=None, artifact_kind=None,
        message_id=None, limit=50, since=0, user=_user(), db=db,
    ))

    assert result["data"]["state"] == "workspace_expired"


def test_code_agent_workspace_usage_documentation_defines_run_flow_and_root_boundaries():
    documentation = (Path(__file__).resolve().parents[3] / "docs" / "code-agent-workspace.md").read_text(encoding="utf-8")
    for phrase in (
        "profile=code",
        "code_run_id",
        "/workspace",
        "/workplace",
        "Verifier",
        "Sealed Artifact",
        "target_not_found",
        "不会回退到通用 `/workplace`",
    ):
        assert phrase in documentation


def test_code_result_exposes_terminal_result_card():
    source = (Path(__file__).resolve().parents[1] / "app" / "services" / "code_agent" / "results.py").read_text(encoding="utf-8")
    assert '"result_card"' in source
    assert '"directly_adoptable": directly_adoptable' in source


def test_agent_refs_list_and_detail_expose_only_authorized_project_readiness(monkeypatch):
    db = _db()
    project = _project(db)
    _manifest(db)
    _ssh_ready(db, monkeypatch)
    authorized = _user("admin")
    outsider = _user("mallory", roles="[]", permissions="[]")
    code = Agent(id="code1", name="Code", profile="code", code_project_id=project.id, creator="admin")
    standard = Agent(id="standard1", name="Standard", profile="standard", creator="admin")
    db.add_all([code, standard])
    db.commit()

    refs = asyncio.run(agent_get("refs", None, "all", authorized, db))
    listed = asyncio.run(agent_get("list", None, "all", authorized, db))
    detail = asyncio.run(agent_get("get", code.id, "all", authorized, db))
    hidden_refs = asyncio.run(agent_get("refs", None, "all", outsider, db))

    assert refs["data"]["code_projects"][0]["availability"]["ready"] is True
    code_row = next(row for row in listed["data"] if row["id"] == code.id)
    standard_row = next(row for row in listed["data"] if row["id"] == standard.id)
    assert code_row["code_project_name"] == "Project"
    assert code_row["code_project_availability"]["reason"] == "ready"
    assert detail["data"]["code_project_name"] == "Project"
    assert "code_project_availability" not in standard_row
    assert hidden_refs["data"]["code_projects"] == []


def test_agent_editor_save_switches_existing_agent_only_when_project_is_ready(monkeypatch):
    db = _db()
    project = _project(db)
    agent = Agent(
        id="standard1",
        name="Standard",
        description="unchanged",
        profile="standard",
        creator="admin",
    )
    db.add(agent)
    db.commit()

    missing = asyncio.run(agent_post(
        AgentBody(action="update", id=agent.id, name="Code", profile="code"),
        _user(),
        db,
    ))
    unavailable = asyncio.run(agent_post(
        AgentBody(
            action="update",
            id=agent.id,
            name="Code",
            profile="code",
            code_project_id=project.id,
        ),
        _user(),
        db,
    ))

    db.refresh(agent)
    assert missing["msg"] == "code_project_required"
    assert unavailable["msg"] == "manifest_missing"
    assert agent.name == "Standard"
    assert agent.description == "unchanged"
    assert agent.profile == "standard"
    assert not agent.code_project_id

    _manifest(db)
    _ssh_ready(db, monkeypatch)
    switched = asyncio.run(agent_post(
        AgentBody(
            action="update",
            id=agent.id,
            name="Code",
            description="ready",
            profile="code",
            code_project_id=project.id,
        ),
        _user(),
        db,
    ))
    standard = asyncio.run(agent_post(
        AgentBody(action="create", name="Default Standard"),
        _user(),
        db,
    ))

    assert switched["code"] == 0
    assert switched["data"]["profile"] == "code"
    assert switched["data"]["code_project_id"] == project.id
    assert standard["code"] == 0
    assert standard["data"]["profile"] == "standard"
    assert not standard["data"]["code_project_id"]

    project.enabled = False
    db.commit()
    disabled = asyncio.run(agent_post(
        AgentBody(
            action="update",
            id=agent.id,
            name="Must Not Persist",
            profile="code",
            code_project_id=project.id,
        ),
        _user(),
        db,
    ))
    db.refresh(agent)
    assert disabled["msg"] == "project_disabled"
    assert agent.name == "Code"
    assert agent.profile == "code"
    assert agent.code_project_id == project.id


def test_claude_code_agent_rejects_llm_group_before_save(monkeypatch):
    db = _db()
    project = _project(db)
    _claude_manifest(db)
    _ssh_ready(db, monkeypatch)
    db.add(LLMResource(
        id="group1",
        type="group",
        name="Mixed LLM Group",
        members='["llm1"]',
    ))
    db.commit()

    response = asyncio.run(agent_post(
        AgentBody(
            action="create",
            name="Code",
            profile="code",
            code_project_id=project.id,
            llm="group1",
        ),
        _user(),
        db,
    ))

    assert response["code"] != 0
    assert response["msg"] == "llm_group_not_supported"
    assert response["data"]["reason"] == "llm_group_not_supported"
    assert response["data"]["repair_action"] == "select_single_llm_resource"
    assert db.query(Agent).filter(Agent.name == "Code").first() is None


def test_claude_code_agent_accepts_valid_single_llm(monkeypatch):
    db = _db()
    project = _project(db)
    _claude_manifest(db)
    _ssh_ready(db, monkeypatch)
    db.add(LLMResource(
        id="llm1",
        type="llm",
        name="Claude",
        provider="anthropic",
        base_url="https://api.example.test",
        api_key_enc=encrypt_secret("sk-live-secret-value"),
        model="claude-sonnet-4",
    ))
    db.commit()

    response = asyncio.run(agent_post(
        AgentBody(
            action="create",
            name="Code",
            profile="code",
            code_project_id=project.id,
            llm="llm1",
        ),
        _user(),
        db,
    ))

    assert response["code"] == 0
    assert response["data"]["profile"] == "code"
    assert response["data"]["llm"] == "llm1"


def test_agent_editor_component_submits_profile_and_requires_ready_project():
    component = (
        Path(__file__).resolve().parents[2] / "web" / "src" / "views" / "Agents.vue"
    ).read_text(encoding="utf-8")

    assert 'v-model="form.profile"' in component
    assert "form.profile === 'code'" in component
    assert 'v-model="form.code_project_id"' in component
    assert ":disabled=\"!project.availability?.ready\"" in component
    assert "profile: form.profile || 'standard'" in component
    assert "code_project_id: form.profile === 'code' ? form.code_project_id : ''" in component
    assert "!selectedCodeProject.value?.availability?.ready" in component
    assert "action: 'get', id: row.id" in component
    assert "const currentRow = detail.data || row" in component
    assert "form.coding_runtime" not in component


def test_agent_cards_and_editor_show_code_project_manifest_status_and_repair_link():
    component = (
        Path(__file__).resolve().parents[2] / "web" / "src" / "views" / "Agents.vue"
    ).read_text(encoding="utf-8")

    assert "row.profile === 'code' ? 'Code Agent' : 'Standard'" in component
    assert "row.code_project_name || '项目不可访问'" in component
    assert "projectStatusLabel(row.code_project_availability)" in component
    assert "!row.code_project_availability?.ready" in component
    assert "修复项目配置" in component
    assert 'to="/code-projects"' in component
    assert "form.code_project_availability" in component


def test_agent_chat_shows_code_context_and_moves_result_evidence_into_markdown():
    component = (
        Path(__file__).resolve().parents[2] / "web" / "src" / "views" / "AgentChat.vue"
    ).read_text(encoding="utf-8")

    assert "agent.code_project_name || '项目不可访问'" in component
    assert "agent.code_project_availability?.ready" in component
    assert "管理 Code Projects" in component
    assert "renderMessage(item.m)" in component
    assert "code-result-banner" not in component
    assert "codeRunResult" not in component
    assert "codeArtifactReview" not in component
    assert "仓库未挂载" not in component
    assert "raw_output" not in component
    assert "runtime_started: '启动 Claude Code Runtime'" in component
    assert "skill_loaded: '加载 Claude Code Skill'" in component
    assert "mcp_loaded: '加载 Claude Code MCP'" in component
    assert "tool_call: 'Claude Code 工具调用'" in component
    assert "file_changed: 'Claude Code 文件变化'" in component
    assert "test_run: 'Claude Code 测试执行'" in component
    assert "verifier_failed_retrying: 'Verifier 失败，继续 Claude Code 修复'" in component
    assert "verifier_passed: 'Verifier 已通过'" in component
    assert "artifact_sealed: '封存工件已生成'" in component
    assert "请先管理 Code Project" in component


def test_manifest_component_rejects_invalid_json_without_silent_fallback():
    component = (
        Path(__file__).resolve().parents[2] / "web" / "src" / "views" / "CodeProjects.vue"
    ).read_text(encoding="utf-8")

    assert "parseJsonField('validation_plan'" in component
    assert "parseJsonField('policy'" in component
    assert "parseJsonField('budgets'" in component
    assert "JSON 数组" in component
    assert "JSON 对象" in component
    assert "if (!payload)" in component
    assert "请先修正 Manifest JSON 字段" in component
    assert "function parseJson(value, fallback)" not in component


def test_control_plane_end_to_end_enables_existing_agent_and_fails_closed(
    monkeypatch,
    tmp_path,
):
    db = _db()
    admin = _user()
    outsider = _user("mallory", roles="[]", permissions="[]")
    agent = Agent(
        id="agent1",
        name="Existing Standard",
        description="keep",
        profile="standard",
        creator="admin",
    )
    db.add(agent)
    db.commit()

    created = asyncio.run(code_project_post(
        CodeProjectBody(
            action="create",
            name="Managed Repository",
            description="Created through control plane",
        ),
        admin,
        db,
    ))
    project_id = created["data"]["id"]
    source_root = tmp_path / "sources"
    source_root.mkdir()
    repository = _git_repository(source_root)
    settings = _local_publish_settings(tmp_path, source_root)
    monkeypatch.setattr(
        app.services.code_agent.manifest_publish,
        "get_settings",
        lambda: settings,
    )
    monkeypatch.setattr(app.routers.code_project, "get_settings", lambda: settings)
    monkeypatch.setattr(control_plane, "get_settings", lambda: settings)
    project = db.get(CodeProject, project_id)
    draft = _save_local_draft(db, project, repository, admin)
    published = asyncio.run(code_project_post(
        CodeProjectBody(
            action="publish",
            project_id=project_id,
            manifest_id=draft["data"]["id"],
        ),
        admin,
        db,
    ))
    switched = asyncio.run(agent_post(
        AgentBody(
            action="update",
            id=agent.id,
            name=agent.name,
            description=agent.description,
            profile="code",
            code_project_id=project_id,
        ),
        admin,
        db,
    ))
    tasks = BackgroundTasks()
    submitted = asyncio.run(chat_post(
        ChatBody(
            action="submit_chat",
            agent_id=agent.id,
            session_id="session1",
            message="Fix the failing test",
        ),
        tasks,
        action=None,
        user=admin,
        db=db,
    ))

    assert created["data"]["availability"]["reason"] == "manifest_missing"
    assert draft["data"]["status"] == "draft"
    assert published["data"]["status"] == "published"
    assert switched["data"]["profile"] == "code"
    assert switched["data"]["code_project_id"] == project_id
    assert submitted["code"] == 0
    assert submitted["data"]["status"] == "pending"
    assert tasks.tasks[0].func.__name__ == "_enqueue_code_chat"
    run = db.get(CodeAgentRun, submitted["data"]["code_run_id"])
    assert run.manifest_id == published["data"]["id"]
    assert run.manifest_version == published["data"]["version"]
    assert json.loads(run.task_contract)["objective"] == "Fix the failing test"

    run_count = db.query(CodeAgentRun).count()
    unauthorized = asyncio.run(chat_post(
        ChatBody(
            action="submit_chat",
            agent_id=agent.id,
            session_id="session2",
            message="Do not disclose this project",
        ),
        BackgroundTasks(),
        action=None,
        user=outsider,
        db=db,
    ))
    disabled = asyncio.run(code_project_post(
        CodeProjectBody(action="set_enabled", id=project_id, enabled=False),
        admin,
        db,
    ))
    disabled_submit = asyncio.run(chat_post(
        ChatBody(
            action="submit_chat",
            agent_id=agent.id,
            session_id="session3",
            message="Must not start while disabled",
        ),
        BackgroundTasks(),
        action=None,
        user=admin,
        db=db,
    ))

    unpublished_project = asyncio.run(code_project_post(
        CodeProjectBody(action="create", name="Unpublished Repository"),
        admin,
        db,
    ))
    agent.code_project_id = unpublished_project["data"]["id"]
    db.commit()
    unpublished_submit = asyncio.run(chat_post(
        ChatBody(
            action="submit_chat",
            agent_id=agent.id,
            session_id="session4",
            message="Must not start without a Manifest",
        ),
        BackgroundTasks(),
        action=None,
        user=admin,
        db=db,
    ))

    assert unauthorized["msg"] == "code_run_unauthorized"
    assert disabled["data"]["availability"]["reason"] == "project_disabled"
    assert disabled_submit["msg"] == "project_unavailable"
    assert unpublished_submit["msg"] == "manifest_missing"
    assert db.query(CodeAgentRun).count() == run_count
