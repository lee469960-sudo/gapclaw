## MODIFIED Requirements

### Requirement: 修改结果必须经独立 Verifier 裁决

CodeAgent 修改任务 SHALL 在创建可写 runner 前对导入源码执行秘密扫描，并在交付前对 canonical diff 和所有待封存新增内容再次执行完整秘密扫描及任务契约和项目策略指定的 Verifier。Verifier SHALL 依据确定性执行事实裁决验证，不得由模型的“测试已通过”文本替代。

源码阶段扫描结果 SHALL 受该 run 冻结的 `secret_policy.source` 与 `secret_policy.source_unscannable` 约束：默认均为 block，显式 warn 时允许带 source finding 或不完整的二进制/不支持格式警告继续执行与封装。截断、超时、扫描崩溃及其他非 warn 允许的不完整原因 SHALL 仍 fail closed。

Patch 阶段（canonical diff 与待封存新增内容）SHALL 始终 fail closed：秘密命中、扫描截断/不支持/失败或不完整时 MUST NOT 产生 `patch_ready`。策略、路径或测试完整性检查失败时 SHALL fail closed 并阻止成功交付。

#### Scenario: 导入源码命中秘密

- **WHEN** 冻结源码快照在创建可写 runner 前的扫描中命中秘密
- **THEN** 若冻结政策未将 `secret_policy.source` 设为 warn，系统以明确安全结果结束准备，且不创建可写 runner
- **AND** 若冻结政策将 `secret_policy.source` 设为 warn，且扫描在该政策下视为可接受，系统允许继续可写执行（其余门禁通过时），后续封装不得仅因这些 source finding 将证据视为缺失
- **AND** 不向模型暴露命中内容

#### Scenario: 导入源码命中秘密且 source 政策为 block

- **WHEN** 冻结源码快照在创建可写 runner 前的扫描中命中秘密，且冻结政策未将 `secret_policy.source` 设为 warn
- **THEN** 系统以明确安全结果结束准备
- **AND** 不创建可写 runner、不向模型暴露命中内容

#### Scenario: 导入源码命中秘密且 source 政策为 warn

- **WHEN** 冻结源码快照的 source 扫描命中秘密，且冻结政策将 `secret_policy.source` 设为 warn，且扫描在该政策下视为可接受
- **THEN** 系统允许继续可写执行（若其余门禁通过）
- **AND** 后续封装不得仅因这些 source finding 将证据视为缺失
- **AND** 不向模型或用户可见输出暴露秘密值

#### Scenario: 模型声称测试通过但 Verifier 未通过

- **WHEN** 模型声明任务已完成，但 Verifier 未运行或报告失败
- **THEN** 系统不得将运行标记为 `patch_ready`
- **AND** 向用户呈现 Verifier 的事实结果和可行动终态

#### Scenario: 验证通过但触及受保护路径

- **WHEN** 测试通过但 diff 违反项目受保护路径或测试完整性规则
- **THEN** Verifier 阻止交付并产生策略失败结果

#### Scenario: 大文件或二进制变更无法完整扫描

- **WHEN** 导入源码或待封存变更包含超限大文件、二进制或不支持格式，或秘密扫描发生截断、超时、执行失败或结果不完整
- **THEN** Verifier 不得将未完整扫描的内容视为无秘密
- **AND** 源码阶段仅当冻结 `secret_policy.source_unscannable` 为 warn 且失败原因属于该政策允许的不可扫描警告时，系统才可继续可写执行与后续封装
- **AND** patch 阶段不完整/失败，以及源码阶段未被 warn 覆盖的不完整原因，不得进入可写执行或产生 `patch_ready`

#### Scenario: 源码含不可扫描文件且 source_unscannable 为 warn

- **WHEN** 导入源码包含二进制或不支持格式导致 source 扫描不完整，且冻结政策将 `secret_policy.source_unscannable` 设为 warn，且失败原因属于该政策允许的不可扫描警告
- **THEN** 系统不得将未完整扫描的内容视为已证明无秘密
- **AND** 若其余门禁通过，系统仍允许可写执行与后续封装
- **AND** 封装不得仅因该 source 扫描不完整将证据视为缺失

#### Scenario: 源码扫描不完整且政策为 block 或原因不受 warn 覆盖

- **WHEN** 导入源码的秘密扫描发生截断、超时、执行失败，或不完整原因未被冻结的 `source_unscannable=warn` 覆盖，或该政策为 block
- **THEN** 系统以明确的策略或验证非成功结果结束
- **AND** 不得进入可写执行或产生 `patch_ready`

#### Scenario: 待封存变更无法完整扫描或 patch 扫描失败

- **WHEN** 待封存变更包含超限大文件、二进制或不支持格式，或 patch 秘密扫描发生截断、超时、执行失败或结果不完整
- **THEN** 系统不得将未完整扫描的 patch 内容视为无秘密
- **AND** 不得产生 `patch_ready`

#### Scenario: patch 扫描命中秘密

- **WHEN** canonical diff 或待封存新增内容在交付前扫描中命中秘密
- **THEN** 系统阻止 `patch_ready` 并记录脱敏安全事实
- **AND** 不在用户可见输出、日志或工件元数据中暴露秘密值
- **AND** 即使 source 政策为 warn，该结果仍成立

### Requirement: 可交付 patch 必须由封存 Workspace 生成

系统 SHALL 从通过验证的冻结 Workspace 生成 canonical diff，并关联基线 commit、diff hash、Verifier 报告和有效策略版本。封装对 source 扫描与 snapshot 的证据检查 SHALL 与冻结 `secret_policy.source` / `source_unscannable` 一致，且 MUST NOT 在封装时对已封存 snapshot 做全量重扫。只有完整工件集合存在时，运行才 SHALL 进入 `patch_ready`；canonical diff 为空且其余证据齐全时 SHALL 进入 `no_change_justified` 而非基础设施失败。

#### Scenario: 成功封存可交付 patch

- **WHEN** Workspace 通过策略与 Verifier 检查，canonical diff 非空，且验证报告均已封存
- **THEN** 系统生成关联基线 commit 和工件哈希的 `patch_ready` 结果

#### Scenario: source warn 政策下封装成功

- **WHEN** Verifier 已通过，snapshot 已封存，冻结政策将 source 与/或 source_unscannable 设为 warn，且 source 扫描结果在该政策下可接受，且 patch 扫描完整且无秘密命中
- **THEN** 系统完成封装
- **AND** 终态为 `patch_ready`（有 diff）或 `no_change_justified`（空 diff）
- **AND** 不因 source finding 或允许的 source 不可扫描警告标记封装证据缺失

#### Scenario: 默认 block 时 source 证据不足不得封装

- **WHEN** 封装时 source 扫描在默认 block 政策下不完整或存在 source finding，或 snapshot / 镜像 digest / 策略哈希等其余封存证据缺失
- **THEN** 系统不得产生 `patch_ready` 或 `no_change_justified`
- **AND** 不得将任何部分产物标记为可直接采用

#### Scenario: 封存期间基础设施失败

- **WHEN** diff、hash 或验证工件在封存期间缺失或上传失败
- **THEN** 系统标记 `infrastructure_error`
- **AND** 不得将任何部分产物标记为 `patch_ready`
