# code-agent-verification Specification

## Purpose

定义 CodeAgent 修改结果的强制验证、不可变 patch 封存与终态语义，防止模型文本声明替代可复核的系统证据。

## Requirements

### Requirement: 修改结果必须经独立 Verifier 裁决

CodeAgent 修改任务 SHALL 在创建可写 runner 前对导入源码执行秘密扫描，并在交付前对 canonical diff 和所有待封存新增内容再次执行完整秘密扫描及任务契约和项目策略指定的 Verifier。Verifier SHALL 依据确定性执行事实裁决验证，不得由模型的“测试已通过”文本替代。

源码阶段扫描结果 SHALL 受该 run 冻结的 `secret_policy.source` 与 `secret_policy.source_unscannable` 约束：默认均为 block，显式 warn 时允许带 source finding 或不完整的二进制/不支持格式警告继续执行与封装。截断、超时、扫描崩溃及其他非 warn 允许的不完整原因 SHALL 仍 fail closed。

Patch 阶段（canonical diff 与待封存新增内容）SHALL 受该 run 冻结的 `secret_policy.patch` 约束：默认 block。显式 `patch=warn` 时，**完整**扫描的秘密命中不得单独阻止验证通过或封装。扫描截断、不支持、失败或不完整时 MUST NOT 产生 `patch_ready`（本能力不提供 `patch_unscannable`）。策略、路径或测试完整性检查失败时 SHALL fail closed 并阻止成功交付。政策合并 SHALL 允许 `secret_policy.patch` 为 `block` 或 `warn`；未出现该字段时平台默认保持 `block`。

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
- **THEN** 系统不得将未完整扫描的内容视为无秘密
- **AND** 对导入源码，若冻结政策未将对应 source 不完整原因显式设为 warn，系统不得进入可写执行
- **AND** 对待封存 patch 内容，即使 `secret_policy.patch` 为 warn，也不得产生 `patch_ready`

#### Scenario: 源码含不可扫描文件且 source_unscannable 为 warn

- **WHEN** 导入源码包含二进制或不支持格式导致 source 扫描不完整，且冻结政策将 `secret_policy.source_unscannable` 设为 warn，且失败原因属于该政策允许的不可扫描警告
- **THEN** 系统不得将未完整扫描的内容视为已证明无秘密
- **AND** 若其余门禁通过，系统仍允许可写执行与后续封装
- **AND** 封装不得仅因该 source 扫描不完整将证据视为缺失

#### Scenario: 源码扫描不完整且政策为 block 或原因不受 warn 覆盖

- **WHEN** 导入源码的秘密扫描发生截断、超时、执行失败，或不完整原因未被冻结的 `source_unscannable=warn` 覆盖，或该政策为 block
- **THEN** 系统以明确的策略或验证非成功结果结束
- **AND** 不得进入可写执行或产生 `patch_ready`

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

### Requirement: Claude Code 完成必须经现有 Verifier 和 Sealer 裁决

当 CodeAgent 使用 Claude Code runtime 时，Claude Code coding completed SHALL 只表示编码阶段结束。系统 SHALL 在每次 coding completed 后运行现有 CodeAgent Verifier，并仅在 Verifier 通过且现有 Sealer 成功封存 canonical diff、Verifier report、策略和工件哈希后进入 `patch_ready`。

#### Scenario: Claude Code 声称完成

- **WHEN** Claude Code 返回成功退出码或在文本中声明任务完成
- **THEN** 系统不得直接标记 `patch_ready`
- **AND** 必须运行现有 Verifier 与 Sealer 后才能产生成功交付

#### Scenario: Verifier 和 Sealer 通过

- **WHEN** Claude Code coding completed 后现有 Verifier 通过且 Sealer 成功封存工件
- **THEN** 系统进入 `patch_ready`
- **AND** 结果展示包含 Verifier 与 sealed artifact 证据

### Requirement: Verifier 失败必须触发受控 Claude Code 自修复闭环

当 Claude Code coding completed 后 Verifier 失败且 retry 预算未耗尽时，系统 SHALL 将脱敏 Verifier report、失败命令、失败摘要、相关变更文件和策略失败事实反馈给同一 Claude Code session 继续修复。默认最多允许 2 次 verifier retry；达到上限或预算耗尽后 SHALL 进入稳定非成功终态。

#### Scenario: Verifier 失败后继续同一 session

- **WHEN** Verifier 失败且 run 仍有 retry、时间、工具调用和资源预算
- **THEN** 系统将脱敏失败反馈注入同一 Claude Code session
- **AND** Claude Code 在同一 Workspace 与相同冻结策略下继续修复

#### Scenario: Retry 后通过

- **WHEN** Claude Code 在 retry 后完成修复且 Verifier 与 Sealer 均通过
- **THEN** 系统进入 `patch_ready`
- **AND** 审计记录 retry 次数、失败摘要和最终通过事实

#### Scenario: Retry 耗尽

- **WHEN** Verifier retry 达到上限或预算耗尽后仍未通过
- **THEN** 系统进入 `verification_failed`、`budget_exhausted` 或对应稳定非成功终态
- **AND** 未验证 patch 不得被呈现为可直接采用

### Requirement: Verifier feedback 和 runtime evidence 必须脱敏

系统 SHALL 在向 Claude Code 回传 Verifier failure、向用户展示 runtime evidence、保存 transcript 或写入审计前执行脱敏。脱敏不得替代 source、patch 和 artifact 的现有秘密扫描；扫描与 Verifier 仍为最终安全门禁。

#### Scenario: Verifier report 包含敏感样式内容

- **WHEN** Verifier failure feedback 中包含 secret-like 内容、路径敏感信息或未授权配置
- **THEN** 系统只向 Claude Code 和用户展示脱敏版本
- **AND** 保留足够的失败类型、命令和位置摘要用于修复

#### Scenario: Transcript 需要审计

- **WHEN** Claude Code runtime 产生 transcript
- **THEN** 系统保存可审计的脱敏 transcript 或摘要
- **AND** 原始 transcript 如被保留，必须限制在 run-local 短期保留或受控审计范围内

### Requirement: 验证不充分必须与成功结果区分

系统 SHALL 将 `verification_inconclusive`、`baseline_broken`、`budget_exhausted`、`stale`、`no_progress` 和策略/基础设施失败与 `patch_ready` 区分。未完成验证的 patch 如被保留供参考 SHALL 显著标记为不可直接采用。

#### Scenario: 验证依赖不可用

- **WHEN** 必要验证因受管环境或依赖不可用而无法完成
- **THEN** 系统以 `verification_inconclusive` 或相应事实终态结束
- **AND** 不将该 patch 计为已验证成功

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
