## ADDED Requirements

### Requirement: 目标未找到或需要用户决策不得产生可采用 patch

当 CodeAgent run 以 `target_not_found` 或 `needs_user_decision` 结束时，系统 SHALL 不生成 sealed patch，不展示为 `patch_ready`，也不得把说明性文件写入业务 repository 作为交付物。若需要向用户提供后续验证命令，系统 SHALL 仅在最终结果中以文本块展示，不将其写入 repo，也不将其作为自动 Verifier 门禁。

#### Scenario: target_not_found 不生成 patch

- **WHEN** run 因目标值未找到而结束
- **THEN** 系统不生成 canonical diff artifact 或 sealed patch
- **AND** 用户结果展示为 `target_not_found`

#### Scenario: needs_user_decision 不生成 patch

- **WHEN** run 因多候选或范围不清而结束
- **THEN** 系统不生成可采用 patch
- **AND** 用户结果展示候选摘要和需要确认的问题

#### Scenario: host validation 仅作为文本输出

- **WHEN** run 成功修改业务文件并需要用户在宿主机验证
- **THEN** 系统可在最终结果中展示 `host-validate.sh` 内容块
- **AND** 不把该脚本写入业务 repository
- **AND** 不将宿主机验证命令作为自动 `patch_ready` 门禁
