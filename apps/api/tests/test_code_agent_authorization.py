"""OpenSpec task 2.1: CodeAgent service-boundary authorization."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Agent, CodeAgentRun, CodeControlAudit, CodeProject
from app.routers.agent_chat import ChatBody, chat_post
from app.services.code_agent.authorization import (
    CodeAuthorizationError,
    can_view_code_project,
    require_platform_admin,
    require_project_operator,
    require_project_owner,
    require_project_reviewer,
)
from app.services.code_agent.control_plane import create_code_run


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _user(username: str, role: str, organization_id: str = "org-a"):
    return SimpleNamespace(
        username=username,
        roles=json.dumps([role]),
        organization_id=organization_id,
    )


def _project(**overrides):
    values = {
        "id": "project-a",
        "name": "Project A",
        "organization_id": "org-a",
        "creator": "alice",
        "visibility": "private",
        "allowed_users": json.dumps(["delegated", "operator", "reviewer", "low"]),
    }
    values.update(overrides)
    return CodeProject(**values)


@pytest.mark.parametrize(
    "resource_kind",
    [
        "source_allowlist",
        "local_roots",
        "secret_metadata",
        "secret_assignment",
        "trusted_image",
    ],
)
def test_only_platform_admin_manages_security_configuration_without_detail_leak(resource_kind):
    db = _db()
    admin = _user("root", "admin", "platform")
    operator = _user("operator", "operator")

    require_platform_admin(admin, db=db, resource_kind=resource_kind)
    with pytest.raises(CodeAuthorizationError) as denied:
        require_platform_admin(operator, db=db, resource_kind=resource_kind)

    assert denied.value.reason == "code_admin_unauthorized"
    audit = db.query(CodeControlAudit).one()
    payload = json.loads(audit.details)
    assert audit.action == "security_failure"
    assert payload["stage"] == "authorization"
    assert payload["reason"] == "authorization_denied"
    assert payload["facts"] == {"operation": f"admin:{resource_kind}"}
    assert "secret" not in audit.details.lower() or "secret_" in resource_kind


def test_owner_operator_and_reviewer_are_separated_at_the_service_boundary():
    db = _db()
    project = _project()
    db.add(project)
    db.commit()
    creator = _user("alice", "user")
    delegated_owner = _user("delegated", "owner")
    operator = _user("operator", "operator")
    reviewer = _user("reviewer", "reviewer")
    low_role = _user("low", "user")

    assert require_project_owner(creator, project, db=db) is project
    assert require_project_owner(delegated_owner, project, db=db) is project
    assert require_project_operator(operator, project, db=db) is project
    assert require_project_reviewer(reviewer, project, db=db) is project

    with pytest.raises(CodeAuthorizationError, match="code_project_not_found"):
        require_project_owner(operator, project, db=db)
    with pytest.raises(CodeAuthorizationError, match="code_run_unauthorized"):
        require_project_operator(reviewer, project, db=db)
    with pytest.raises(CodeAuthorizationError, match="code_artifact_unauthorized"):
        require_project_reviewer(low_role, project, db=db)


@pytest.mark.parametrize("role,reason", [
    ("owner", "code_project_not_found"),
    ("operator", "code_run_unauthorized"),
    ("reviewer", "code_artifact_unauthorized"),
])
def test_cross_organization_access_is_indistinguishable_from_missing_resource(role, reason):
    db = _db()
    project = _project(visibility="public")
    db.add(project)
    db.commit()
    subject = _user("alice" if role == "owner" else role, role, "org-b")
    gate = {
        "owner": require_project_owner,
        "operator": require_project_operator,
        "reviewer": require_project_reviewer,
    }[role]

    with pytest.raises(CodeAuthorizationError) as cross_org:
        gate(subject, project, db=db)
    with pytest.raises(CodeAuthorizationError) as missing:
        gate(subject, None, db=db)

    assert cross_org.value.reason == missing.value.reason == reason
    assert can_view_code_project(subject, project) is False


def test_cross_project_run_and_artifact_resources_are_rejected_without_resource_details():
    db = _db()
    project = _project()
    db.add(project)
    db.commit()
    other_run = SimpleNamespace(id="sensitive-run", project_id="project-b")
    other_artifact = SimpleNamespace(id="sensitive-artifact", project_id="project-b")

    with pytest.raises(CodeAuthorizationError) as run_denied:
        require_project_operator(
            _user("operator", "operator"), project, db=db, resource=other_run
        )
    with pytest.raises(CodeAuthorizationError) as artifact_denied:
        require_project_reviewer(
            _user("reviewer", "reviewer"), project, db=db, resource=other_artifact
        )

    assert run_denied.value.reason == "code_run_unauthorized"
    assert artifact_denied.value.reason == "code_artifact_unauthorized"
    audit_payload = " ".join(row.details for row in db.query(CodeControlAudit).all())
    assert "sensitive-run" not in audit_payload
    assert "sensitive-artifact" not in audit_payload


def test_code_run_cancellation_requires_an_in_scope_operator(monkeypatch):
    db = _db()
    project = _project()
    db.add_all([
        project,
        Agent(
            id="agent-a",
            name="Code",
            profile="code",
            code_project_id=project.id,
        ),
        CodeAgentRun(
            id="run-a",
            agent_id="agent-a",
            session_id="session-a",
            project_id=project.id,
            manifest_id="manifest-a",
            manifest_version=1,
            status="running",
        ),
    ])
    db.commit()
    stopped: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "app.routers.agent_chat.stop_chat",
        lambda agent_id, session_id: stopped.append((agent_id, session_id)),
    )
    body = ChatBody(action="stop_chat", agent_id="agent-a", session_id="session-a")

    denied = asyncio.run(chat_post(
        body,
        BackgroundTasks(),
        action=None,
        user=_user("reviewer", "reviewer"),
        db=db,
    ))
    allowed = asyncio.run(chat_post(
        body,
        BackgroundTasks(),
        action=None,
        user=_user("operator", "operator"),
        db=db,
    ))

    assert denied["msg"] == "code_run_unauthorized"
    assert allowed["code"] == 0
    assert stopped == [("agent-a", "session-a")]


def test_direct_run_service_rejects_low_role_before_manifest_or_run_details_are_read():
    db = _db()
    project = _project()
    agent = Agent(
        id="agent-a",
        name="Code",
        profile="code",
        code_project_id=project.id,
    )
    db.add_all([project, agent])
    db.commit()

    with pytest.raises(CodeAuthorizationError) as denied:
        create_code_run(
            db,
            agent=agent,
            actor=_user("reviewer", "reviewer"),
            project_id=project.id,
            objective="Do not reveal whether a Manifest exists",
        )

    assert denied.value.reason == "code_run_unauthorized"
    assert db.query(CodeAgentRun).count() == 0
    audit = db.query(CodeControlAudit).one()
    assert audit.project_id == project.id
    payload = json.loads(audit.details)
    assert payload["stage"] == "authorization"
    assert payload["reason"] == "authorization_denied"
    assert payload["facts"] == {"operation": "run:create"}
