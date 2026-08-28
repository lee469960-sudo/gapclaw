"""OpenSpec task 2.4: stable failures and sanitized security audit payloads."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import CodeControlAudit
from app.services.code_agent.failures import (
    FailureReason,
    FailureStage,
    external_failure,
    failure_descriptor,
    record_code_failure,
    sanitized_audit_details,
)
from app.services.code_agent.results import serialize_code_result


@pytest.mark.parametrize(
    ("reason", "stage"),
    [
        (FailureReason.AUTHORIZATION_DENIED, FailureStage.AUTHORIZATION),
        (FailureReason.SOURCE_NOT_ALLOWED, FailureStage.SOURCE),
        (FailureReason.REPOSITORY_NETWORK_POLICY_DENIED, FailureStage.SOURCE),
        (FailureReason.SOURCE_UNREACHABLE, FailureStage.SOURCE),
        (FailureReason.SOURCE_LIMIT_EXCEEDED, FailureStage.SOURCE),
        (FailureReason.REPOSITORY_FEATURE_UNSUPPORTED, FailureStage.SOURCE),
        (FailureReason.REPOSITORY_AUTH_FAILED, FailureStage.AUTH),
        (FailureReason.REPOSITORY_REF_INVALID, FailureStage.REF),
        (FailureReason.SNAPSHOT_INVALID, FailureStage.SNAPSHOT),
        (FailureReason.IMAGE_DIGEST_INVALID, FailureStage.IMAGE),
        (FailureReason.WORKSPACE_MOUNT_INVALID, FailureStage.MOUNT),
        (FailureReason.WORKSPACE_INTEGRITY_ERROR, FailureStage.MOUNT),
        (FailureReason.SOURCE_SCAN_FAILED, FailureStage.SCAN),
        (FailureReason.PATCH_SCAN_FAILED, FailureStage.SCAN),
        (FailureReason.SECRET_DETECTED, FailureStage.SCAN),
        (FailureReason.RUNNER_POLICY_INVALID, FailureStage.RUNNER),
        (FailureReason.RUNNER_UNAVAILABLE, FailureStage.RUNNER),
        (FailureReason.RESOURCE_LIMIT_EXCEEDED, FailureStage.RESOURCE),
        (FailureReason.BUDGET_EXHAUSTED, FailureStage.RESOURCE),
        (FailureReason.VERIFIER_FAILED, FailureStage.VERIFIER),
        (FailureReason.SANDBOX_CLEANUP_FAILED, FailureStage.CLEANUP),
    ],
)
def test_each_security_stage_has_a_stable_actionable_external_mapping(reason, stage):
    payload = external_failure(reason)

    assert payload["reason"] == reason.value
    assert payload["stage"] == stage.value
    assert payload["detail"]
    assert "token" not in payload["detail"].lower()
    assert failure_descriptor(reason.value).reason is reason


@pytest.mark.parametrize(
    ("legacy_reason", "stable_reason"),
    [
        ("repository_source_not_allowed", "source_not_allowed"),
        ("repository_unreachable", "source_unreachable"),
        ("ref_unavailable", "repository_ref_invalid"),
        ("snapshot_hash_mismatch", "snapshot_invalid"),
        ("image_digest_mismatch", "image_digest_invalid"),
        ("runner_start_failed", "runner_unavailable"),
        ("llm_group_not_supported", "model_unavailable"),
        ("project_concurrency_limit", "resource_limit_exceeded"),
        ("cleanup_failed", "sandbox_cleanup_failed"),
        ("target_not_found", "target_not_found"),
        ("needs_user_decision", "needs_user_decision"),
    ],
)
def test_existing_internal_reasons_map_to_stable_external_reasons(
    legacy_reason,
    stable_reason,
):
    assert external_failure(legacy_reason)["reason"] == stable_reason


def test_security_audit_links_actor_stage_policy_and_trace_but_drops_sensitive_details():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    secret = "sk-example0123456789abcdef"
    policy_hash = "a" * 64

    event = record_code_failure(
        db,
        actor="operator",
        reason=FailureReason.REPOSITORY_AUTH_FAILED,
        project_id="project-a",
        run_id="run-a",
        policy_hash=policy_hash,
        trace_id="trace-123",
        details={
            "operation": "repository:authenticate",
            "reference_id": secret,
            "file_count": 12,
            "password": secret,
            "repository_url": f"https://user:{secret}@git.example.test/repo.git",
            "error": f"authentication rejected {secret}",
        },
    )
    payload = json.loads(event.details)

    assert event.actor == "operator"
    assert event.action == "security_failure"
    assert event.project_id == "project-a"
    assert payload == {
        "facts": {"file_count": 12, "operation": "repository:authenticate"},
        "policy_hash": policy_hash,
        "reason": "repository_auth_failed",
        "run_id": "run-a",
        "stage": "auth",
        "trace_id": "trace-123",
    }
    serialized = json.dumps(payload, sort_keys=True)
    assert secret not in serialized
    assert "git.example.test" not in serialized
    assert db.query(CodeControlAudit).count() == 1


def test_audit_detail_sanitizer_is_allowlist_based_and_redacts_secret_shaped_values():
    secret = "sk-example0123456789abcdef"
    assert sanitized_audit_details({
        "source_id": "source-a",
        "finding_count": 2,
        "error_type": "APIError",
        "error_summary": f"Docker auth failed {secret}",
        "reference_id": secret,
        "stdout": secret,
        "nested": {"password": secret},
    }) == {
        "source_id": "source-a",
        "finding_count": 2,
        "error_type": "APIError",
        "error_summary": "Docker auth failed [redacted]",
    }


def test_result_serialization_never_echoes_unknown_internal_failure_text():
    secret = "sk-example0123456789abcdef"
    run = SimpleNamespace(
        id="run-a",
        project_id="project-a",
        status="infrastructure_error",
        failure_reason=f"unexpected provider error: {secret}",
        manifest_id="manifest-a",
        manifest_version=1,
        verifier_report="{}",
        budget_usage="{}",
    )

    payload = serialize_code_result(run)

    assert payload["failure_reason"] == "infrastructure_error"
    assert payload["failure"]["stage"] == "internal"
    assert secret not in json.dumps(payload, sort_keys=True)
