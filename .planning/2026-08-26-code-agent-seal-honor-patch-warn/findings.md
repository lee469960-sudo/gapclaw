# Findings: code-agent-seal-honor-patch-warn

## Pre-implementation

- 已发布 Manifest 不可变；只改已冻结 run 的 JSON 无效。下一枪需要新 draft + publish，且 `effective_policy.secret_policy.patch=warn`。
- `merge_policy_layers` 当前 `"patch": {"block"}`，`{"secret_policy": {"patch": "warn"}}` 会 `policy_invalid_secret_policy`。
- Verifier `scan_changed_content` 命中秘密时 `complete=False` 且无 `content_hash`；Sealer 要求 `secret_scan_complete` + `content_hash`。warn 路径必须把扫描做成 **complete + detected + hash**。
- Sealer 在 `sealed_scan.findings_count` 与 `content_scan.findings` 两处硬拒绝；后者即使跳过 findings_count 也会 `verifier_content_mismatch`。
- `load_verified_bundle` 要求 source/patch `findings_count == 0` 且 source `complete`。`dbt_test` 的 source 扫描是 incomplete + findings；不改审阅则封存后仍无法 accept。
- 平台默认保持 `patch=block`。不引入 `patch_unscannable`：截断/二进制/失败仍 fail closed。

## During execution

- Targeted pytest 71 passed after implementation.
- WatchFiles 不可靠；已整进程重启 API。
- 未写入 gap.db：已发布 Manifest 不可变。`dbt_test` v1 仍是 `patch: block`。Operator 须在 Code Projects 的 policy JSON 把 `patch` 改为 `warn`，保存草稿并发布，下一 run 才会冻结 warn。
- 用户反馈 Code Projects 里看不到 `dbt_test`：库里项目仍在（`ae834a47`，admin 可见，availability=ready）。当时本机 API `:8000` 已退出（Vite `:5173` 还在），列表接口连不上所以页面空。已重新拉起 API；`GET page_code_project.cgi?action=list` 返回 `dbt_test` / `ready`。

## Verification findings

- OpenSpec strict validation failed because the `MODIFIED` requirement omitted base scenarios. Since `MODIFIED` replaces the full requirement, the delta spec must retain all existing scenarios that are still valid.
- DB verification later showed `dbt_test` Manifest v3 is published with `secret_policy.patch=warn`; run `ba6053d2` froze that policy and reached `patch_ready`.
- Sealed artifact `47fbdc93` exists for run `ba6053d2`, so task 4.1 was complete in runtime state even though `tasks.md` and planning files were stale.
