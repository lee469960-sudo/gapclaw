from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "release.yml"
DEPLOY_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "deploy-production.yml"
STAGING_DEPLOY_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "deploy-staging.yml"


def _workflow() -> str:
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def test_tag_build_requires_exact_version_file_match():
    workflow = _workflow()

    assert 'if [ "${GIT_TAG}" != "${TAG}" ]; then' in workflow
    assert "must exactly equal deploy/gap.version" in workflow


def test_tag_build_emits_digest_pinned_release_manifest_artifact():
    workflow = _workflow()

    assert "id: api-build" in workflow
    assert "id: web-build" in workflow
    assert "API_DIGEST: ${{ steps.api-build.outputs.digest }}" in workflow
    assert "WEB_DIGEST: ${{ steps.web-build.outputs.digest }}" in workflow
    assert '"api_image": f"{os.environ[\'IMAGE_API\']}@{api_digest}"' in workflow
    assert '"web_image": f"{os.environ[\'IMAGE_WEB\']}@{web_digest}"' in workflow
    assert "name: gap-release-manifest" in workflow
    assert "path: .release/release-manifest.json" in workflow


def test_tag_build_emits_a_separate_build_verified_staging_manifest():
    workflow = _workflow()

    assert "name: Create staging release manifest" in workflow
    assert 'manifest["target_id"] = "staging"' in workflow
    assert "name: gap-staging-release-manifest" in workflow
    assert "path: .release/release-manifest-staging.json" in workflow


def test_production_deployment_uses_default_branch_workflow_run_and_fixed_runner():
    workflow = DEPLOY_WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "workflow_run:" in workflow
    assert 'workflows: ["Release"]' in workflow
    assert "types: [completed]" in workflow
    assert "actions: read" in workflow
    assert "environment: production" in workflow
    assert "run-id: ${{ github.event.workflow_run.id }}" in workflow
    assert "github-token: ${{ github.token }}" in workflow
    assert "/opt/gap-runner/bin/gap-deploy-runner deploy --manifest" in workflow


def test_production_deployment_never_checks_out_or_executes_tag_owned_assets():
    workflow = DEPLOY_WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "actions/checkout" not in workflow
    assert "scripts/deploy.sh" not in workflow
    assert "docker-compose.prod.yml" not in workflow
    assert "ALIYUN_REGISTRY_PASSWORD" not in workflow


def test_staging_deployment_uses_protected_environment_and_fixed_runner():
    workflow = STAGING_DEPLOY_WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "workflow_run:" in workflow
    assert 'workflows: ["Release"]' in workflow
    assert "types: [completed]" in workflow
    assert "actions: read" in workflow
    assert "runs-on: [self-hosted, linux, staging]" in workflow
    assert "environment: staging" in workflow
    assert "name: gap-staging-release-manifest" in workflow
    assert "run-id: ${{ github.event.workflow_run.id }}" in workflow
    assert "/opt/gap-staging-runner/bin/gap-deploy-runner deploy --manifest" in workflow


def test_staging_deployment_never_checks_out_or_executes_tag_owned_assets_or_acr_login():
    workflow = STAGING_DEPLOY_WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "actions/checkout" not in workflow
    assert "scripts/deploy.sh" not in workflow
    assert "docker-compose.prod.yml" not in workflow
    assert "acr login" not in workflow.lower()
    assert "ALIYUN_REGISTRY_PASSWORD" not in workflow


def test_tag_build_has_no_legacy_production_deploy_job_or_credential_transfer():
    workflow = _workflow()

    assert "\n  deploy:\n" not in workflow
    assert "runs-on: [self-hosted, linux, production]" not in workflow
    assert "./scripts/deploy.sh" not in workflow
    assert "ALIYUN_REGISTRY_PASSWORD: ${{ secrets.ALIYUN_REGISTRY_PASSWORD }}" not in workflow
