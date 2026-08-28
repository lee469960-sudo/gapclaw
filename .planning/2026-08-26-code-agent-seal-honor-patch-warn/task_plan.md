# Task Plan: code-agent-seal-honor-patch-warn（Execution Map）

## Goal

让冻结的 `secret_policy.patch=warn` 在 Verifier、Sealer 与审阅门槛生效；默认 `patch=block` 与不完整 patch 扫描仍 fail closed。随后把本地 `dbt_test` 已发布 Manifest 的 `patch` 设为 warn。

## Next Step

无。`code-agent-seal-honor-patch-warn` 已归档。

## Current Phase

Phase 3: Live project + API restart（complete）

## Phases

### Phase 1: Spec + honor patch=warn

- **OpenSpec task mapping:** 1.1–2.3
- **Depends on:** none
- **Status:** complete

### Phase 2: Tests

- **OpenSpec task mapping:** 3.1
- **Depends on:** Phase 1
- **Status:** complete

### Phase 3: Live project + API restart

- **OpenSpec task mapping:** 4.1
- **Depends on:** Phase 2
- **Status:** complete

### Phase 4: Verification

- **OpenSpec task mapping:** post-apply verification
- **Depends on:** Phase 3
- **Status:** complete

### Phase 5: Archive

- **OpenSpec task mapping:** archive
- **Depends on:** Phase 4
- **Status:** complete

## Boundaries

- 新 OpenSpec change `code-agent-seal-honor-patch-warn`；不改已完成的 `code-agent-seal-honor-source-warn`。
- 不改平台默认 `patch=block`，不引入 `patch_unscannable`。
- 不改 UI 文案；Operator 通过 Manifest `policy` JSON 显式设 `patch: warn`。

## Per-Task Completion Gate

1. 完成该 task 对应代码或交付物。
2. 运行该 task 声明的测试或可观察验证。
3. 在 `progress.md` 写入实际变更、验证命令和结果。
4. 勾选 `tasks.md`。
