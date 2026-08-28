## 1. Policy merge + scanner fingerprint

- [x] 1.1 Allow `secret_policy.patch` values `{block, warn}` in `merge_policy_layers`; keep platform default `block`; replace the invalid-shape case that expected `patch: warn` to fail; add a merge test that `patch: warn` is accepted
- [x] 1.2 Change `scan_changed_content` so a secret hit still finishes hashing (`complete=True`, `detected=True`, non-empty `content_hash`); truncated/failed/unsupported remain incomplete

## 2. Honor warn at verifier, sealer, review

- [x] 2.1 Verifier: if frozen `patch=warn` and the content scan is complete, do not fail on `detected`; still fail closed on incomplete scans. Add a test that patch warn runs the validation plan despite secret-like content, and keep the default-block secret tests
- [x] 2.2 Sealer: if frozen `patch=warn`, do not reject complete patch scans solely for findings; still reject incomplete patch scans. Add a test that a secret-bearing change seals under warn and `load_verified_bundle` succeeds. Keep `test_frozen_patch_secret_hit_never_produces_patch_ready` for default block
- [x] 2.3 Review: `load_verified_bundle` honors sealed `secret_policy` for source/patch findings and source unscannable completeness (reuse scanner helpers); default zero-finding complete reports still pass

## 3. Run the targeted pytest files

- [x] 3.1 `python -m pytest -q tests/test_code_agent_policy_layers.py tests/test_code_agent_verifier.py tests/test_code_agent_artifacts.py`

## 4. Local project

- [x] 4.1 Restart the local API and publish a new `dbt_test` Manifest with `secret_policy.patch=warn` (keep existing source warn). Confirm the next run freezes `patch: warn`
