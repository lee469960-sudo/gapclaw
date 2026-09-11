from __future__ import annotations

from pathlib import Path


def test_staging_documentation_matches_fixed_assets_and_operational_contract():
    documentation = (Path(__file__).parents[3] / "docs" / "staging-deployment.md").read_text(encoding="utf-8")

    for value in (
        "`staging` GitHub Environment", "self-hosted", "linux", "staging", "AcrPull", "gap-staging-pull",
        "/opt/gap-staging-runner", "/opt/gap-staging", "initialize-staging-host.sh",
        "staging.gapclaw.online", "runner-staging.gapclaw.online", "https://gap-runner-staging.internal:9443",
        "Caddy", "Caddyfile.staging", "install-staging-caddy.sh", "/etc/caddy/staging/runner-ca.crt", "gap-deploy-runner-staging",
        "gap-deploy-runner deploy --manifest", "gap-deploy-runner rollback", "reconciliation_required",
    ):
        assert value in documentation


def test_staging_documentation_preserves_protected_workflow_and_no_secret_evidence_rules():
    documentation = (Path(__file__).parents[3] / "docs" / "staging-deployment.md").read_text(encoding="utf-8")

    assert "不 checkout tag" in documentation
    assert "不登录 ACR" in documentation
    assert "不含秘密的证据" in documentation
    assert "不要在撤销或故障处理中执行 production 命令" in documentation
    assert "不得启动 Nginx 容器或加载 Nginx 配置" in documentation
