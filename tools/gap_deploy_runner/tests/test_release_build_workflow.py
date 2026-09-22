from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "release.yml"


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


def test_tag_build_delivers_only_a_canonical_signed_manifest_to_the_fixed_hook():
    workflow = _workflow()

    assert "name: Deliver signed manifest to GAP Hook" in workflow
    assert "RELEASE_HOOK_SECRET: ${{ secrets.GAP_RELEASE_HOOK_SECRET }}" in workflow
    assert 'DELIVERY_ID: release-${{ github.run_id }}-${{ github.run_attempt }}' in workflow
    assert 'json.dumps(envelope, sort_keys=True, separators=(",", ":"))' in workflow
    assert "hmac.new(" in workflow
    assert '"https://gapclaw.online/internal/release-hook"' in workflow
    assert "X-GAP-Release-Signature" in workflow
    assert "--data-binary @.release/release-hook.json" in workflow


def test_hook_delivery_retries_transient_failures_with_the_same_signed_envelope():
    workflow = _workflow()
    delivery = workflow.split("      - name: Deliver signed manifest to GAP Hook", 1)[1]
    retry_loop = delivery.split('for delay_seconds in "${retry_delays[@]}"; do', 1)[1]

    assert 'retry_delays=(0 5 10 20 40)' in delivery
    assert 'retryable_http_codes=("403" "408" "409" "425" "429")' in delivery
    assert '"${http_code}" =~ ^5[0-9][0-9]$' in delivery
    assert "curl_exit=0" in delivery
    assert "--max-time 30" in delivery
    assert "--retry" not in delivery
    assert 'hook_signature="$(<.release/release-hook.signature)"' in delivery
    assert '--header "X-GAP-Release-Signature: ${hook_signature}"' in retry_loop
    assert "--data-binary @.release/release-hook.json" in retry_loop
    assert 'echo "release hook transient failure curl_exit=${curl_exit} http_code=${http_code}; retrying same signed delivery_id=${DELIVERY_ID}"' in delivery
    assert delivery.count('"delivery_id": os.environ["DELIVERY_ID"]') == 1
    assert delivery.count('with open(".release/release-hook.json", "wb")') == 1
    assert delivery.count('with open(".release/release-hook.signature", "w", encoding="utf-8")') == 1
    assert "datetime.now" not in retry_loop
    assert "hmac.new(" not in retry_loop


def test_hook_delivery_has_no_registry_credentials_or_tag_controlled_host_execution():
    workflow = _workflow()

    delivery = workflow.split("      - name: Deliver signed manifest to GAP Hook", 1)[1]

    assert "ALIYUN_REGISTRY_USERNAME" not in delivery
    assert "ALIYUN_REGISTRY_PASSWORD" not in delivery
    assert "scripts/deploy.sh" not in delivery
    assert "docker-compose.prod.yml" not in delivery
    assert "/opt/gap-runner" not in delivery
    assert '"target_id": os.environ["TARGET_ID"]' in workflow


def test_release_has_no_workflow_run_or_self_hosted_runner_dependency():
    workflow_dir = REPO_ROOT / ".github" / "workflows"
    workflows = "\n".join(path.read_text(encoding="utf-8") for path in workflow_dir.glob("*.yml"))

    assert "group: release-build" in _workflow()
    assert "group: production-deploy" not in _workflow()
    assert not (workflow_dir / "deploy-production.yml").exists()
    assert not (workflow_dir / "deploy-staging.yml").exists()
    assert "workflow_run:" not in workflows
    assert "self-hosted" not in workflows
    assert "environment: production" not in workflows
    assert "registration-token" not in workflows


def test_tag_can_only_contribute_to_the_manifest_not_host_execution():
    workflow = _workflow()
    delivery = workflow.split("      - name: Deliver signed manifest to GAP Hook", 1)[1]

    assert "TARGET_ID: production" in workflow
    assert '"target_id": os.environ["TARGET_ID"]' in workflow
    assert "${{ github.ref_name }}" not in delivery
    assert "${GITHUB_REF_NAME}" not in delivery
    assert "/opt/gap-runner" not in delivery
    assert "compose" not in delivery.lower()
    assert "deploy.sh" not in delivery
    assert "https://gapclaw.online/internal/release-hook" in delivery
    assert "${{ secrets.GAP_RELEASE_HOOK_URL }}" not in delivery
