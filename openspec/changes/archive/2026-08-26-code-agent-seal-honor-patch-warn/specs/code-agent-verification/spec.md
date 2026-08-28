## MODIFIED Requirements

### Requirement: 修改结果必须经独立 Verifier 裁决

CodeAgent 修改任务 SHALL 在创建可写 runner 前对导入源码执行秘密扫描，并在交付前对 canonical diff 和所有待封存新增内容再次执行完整秘密扫描及任务契约和项目策略指定的 Verifier。Verifier SHALL 依据确定性执行事实裁决验证，不得由模型的“测试已通过”文本替代。

源码阶段扫描结果 SHALL 受该 run 冻结的 `secret_policy.source` 与 `secret_policy.source_unscannable` 约束：默认均为 block，显式 warn 时允许带 source finding 或不完整的二进制/不支持格式警告继续执行与封装。截断、超时、扫描崩溃及其他非 warn 允许的不完整原因 SHALL 仍 fail closed。

Patch 阶段（canonical diff 与待封存新增内容）SHALL 受该 run 冻结的 `secret_policy.patch` 约束：默认 block。显式 `patch=warn` 时，**完整**扫描的秘密命中不得单独阻止验证通过或封装。扫描截断、不支持、失败或不完整时 MUST NOT 产生 `patch_ready`（本能力不提供 `patch_unscannable`）。策略、路径或测试完整性检查失败时 SHALL fail closed 并阻止成功交付。政策合并 SHALL 允许 `secret_policy.patch` 为 `block` 或 `warn`；未出现该字段时平台默认保持 `block`。

#### Scenario: 导入源码命中秘密

- **WHEN** 冻结源码快照在创建可写 runner 前的扫描中命中秘密
- **THEN** 若冻结政策未将 `secret_policy.source` 设为 warn，系统以明确安全结果结束准备，且不创建可写 runner
- **AND** 若冻结政策将 `secret_policy.source` 设为 warn，且扫描在该政策下视为可接受，系统允许继续可写执行（其余门禁通过时），后续封装不得仅因这些 source finding 将证据视为缺失
- **AND** 不向模型暴露命中内容

#### Scenario: 模型声称测试通过但 Verifier 未通过

- **WHEN** 模型声明任务已完成，但 Verifier 未运行或报告失败
- **THEN** 系统不得将运行标记为 `patch_ready`
- **AND** 向用户呈现 Verifier 的事实结果和可行动终态

#### Scenario: 验证通过但触及受保护路径

- **WHEN** 测试通过但 diff 违反项目受保护路径或测试完整性规则
- **THEN** Verifier 阻止交付并产生策略失败结果

#### Scenario: 大文件或二进制变更无法完整扫描

- **WHEN** 导入源码或待封存变更包含超限大文件、二进制或不支持格式，或秘密扫描发生截断、超时、执行失败或结果不完整
- **THEN** 系统不得将未完整扫描的内容视为无秘密
- **AND** 对导入源码，若冻结政策未将对应 source 不完整原因显式设为 warn，系统不得进入可写执行
- **AND** 对待封存 patch 内容，即使 `secret_policy.patch` 为 warn，也不得产生 `patch_ready`

#### Scenario: patch 扫描命中秘密

- **WHEN** canonical diff 或待封存新增内容在交付前扫描中命中秘密
- **THEN** 若冻结政策未将 `secret_policy.patch` 设为 warn，系统阻止 `patch_ready` 并记录脱敏安全事实
- **AND** 若冻结政策将 `secret_policy.patch` 设为 warn，且扫描完整，系统不得仅因该命中阻止验证通过、封装或审阅
- **AND** 不在用户可见输出、日志或工件元数据中暴露秘密值

#### Scenario: patch 扫描命中秘密且政策为 block

- **WHEN** canonical diff 或待封存新增内容在交付前扫描中命中秘密，且冻结政策未将 `secret_policy.patch` 设为 warn
- **THEN** 系统阻止 `patch_ready` 并记录脱敏安全事实
- **AND** 不在用户可见输出、日志或工件元数据中暴露秘密值
- **AND** 即使 source 政策为 warn，该结果仍成立

#### Scenario: patch 扫描命中秘密且政策为 warn

- **WHEN** canonical diff 或待封存新增内容在交付前被完整扫描并命中秘密，且冻结政策将 `secret_policy.patch` 设为 warn
- **THEN** Verifier 不得仅因该命中将验证标为失败（其余门禁通过时）
- **AND** 封装不得仅因该完整 patch finding 拒绝封存
- **AND** 审阅不得仅因该完整 patch finding 将工件证据视为不匹配
- **AND** 不在用户可见输出、日志或工件元数据中暴露秘密值

#### Scenario: 待封存变更无法完整扫描或 patch 扫描失败

- **WHEN** 待封存变更包含超限大文件、二进制或不支持格式，或 patch 秘密扫描发生截断、超时、执行失败或结果不完整
- **THEN** 系统不得将未完整扫描的 patch 内容视为无秘密
- **AND** 即使 `secret_policy.patch` 为 warn，也不得产生 `patch_ready`

#### Scenario: 政策合并接受显式 patch=warn

- **WHEN** 某一政策层将 `secret_policy.patch` 设为 `warn`，且其余 secret_policy 字段合法
- **THEN** 合并后的有效政策包含 `patch: warn`
- **AND** 未出现的字段保持平台默认（`source`/`source_unscannable` 为 `block`，`output` 为 `redact`）
