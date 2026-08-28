## ADDED Requirements

### Requirement: OpenSpec archive 不得早于平台封存成功

当 CodeAgent 使用 `claude_code` 且 Workspace 内存在 OpenSpec change 时，系统 SHALL 仅在现有 Sealer 成功封存（`patch_ready` 或 `no_change_justified`）之后执行 `openspec archive`。Claude Code coding session MUST NOT 自行 archive。Sealer 失败、Verifier 失败或 run 非成功终态时 MUST NOT archive。

#### Scenario: 封存成功后 archive

- **WHEN** `claude_code` run 的 Sealer 成功进入 `patch_ready` 或 `no_change_justified`，且 Workspace 存在未归档 OpenSpec change
- **THEN** 系统在 runner 内执行 `openspec archive`
- **AND** 审计记录 archive 结果且不含秘密

#### Scenario: 封存失败不得 archive

- **WHEN** Verifier 失败、Sealer 失败或 run 进入非成功终态
- **THEN** 系统不得执行 `openspec archive`
- **AND** Workspace 中的 OpenSpec change 保持未归档
