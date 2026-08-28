## 1. Sealer source-policy tests

- [x] 1.1 Add a sealer test where frozen `secret_policy.source` and `source_unscannable` are `warn`, the source scan row is incomplete with an allowed unscannable reason and `findings_count > 0`, workspace has no file changes, and verify `CodeArtifactSealer.seal()` returns `sealed=True` with outcome `no_change_justified` and a `CodeArtifact` row exists
- [x] 1.2 Add a sealer test with the same incomplete/findings source scan under default/block policy and verify seal returns `sealed=False`, reason `artifact_evidence_missing`, and no artifact row
- [x] 1.3 Confirm existing patch-secret and missing-field sealer tests still fail closed (`secret_detected` / `artifact_evidence_missing`) by running `pytest -q tests/test_code_agent_artifacts.py` after 1.1–1.2 (expect 1.1 red until implementation)

## 2. Honor warn at encapsulate

- [x] 2.1 In `CodeArtifactSealer.seal()`, replace the hard `source_scan.complete` and `findings_count == 0` checks with the existing `scanner.py` helpers (`_source_scan_complete_or_warned` and `_source_secret_policy_action` / `_source_unscannable_policy_action` on `run.effective_policy`), keep remaining snapshot/image/policy-hash evidence checks, do not re-scan the snapshot, and verify `pytest -q tests/test_code_agent_artifacts.py` passes

## 3. Local shot-1 check

- [x] 3.1 Restart or reload the local API so the sealer change is live, re-run the existing trivial `dbt_test` CodeAgent task in the UI, and verify the terminal result is `no_change_justified` / 「无需代码变更」（or equivalent success), not `infrastructure_error` / 「基础设施失败」
