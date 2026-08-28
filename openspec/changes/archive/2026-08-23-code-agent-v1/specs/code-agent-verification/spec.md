## Purpose

定义 CodeAgent 修改结果的强制验证、不可变 patch 封存与终态语义，防止模型文本声明替代可复核的系统证据。

## ADDED Requirements

### Requirement: 修改结果必须经独立 Verifier 裁决
CodeAgent 修改任务在交付前 SHALL 执行任务契约和项目策略指定的 Verifier。Verifier SHALL 依据确定性执行事实裁决验证，不得由模型的“测试已通过”文本替代；策略、路径或测试完整性检查失败时 SHALL 阻止成功交付。

#### Scenario: 模型声称测试通过但 Verifier 未通过
- **WHEN** 模型声明任务已完成，但 Verifier 未运行或报告失败
- **THEN** 系统不得将运行标记为 `patch_ready`
- **AND** 向用户呈现 Verifier 的事实结果和可行动终态

#### Scenario: 验证通过但触及受保护路径
- **WHEN** 测试通过但 diff 违反项目受保护路径或测试完整性规则
- **THEN** Verifier 阻止交付并产生策略失败结果

#### Scenario: 大文件或二进制变更无法完整扫描
- **WHEN** 待封存变更包含大文件、二进制内容，或秘密扫描发生截断、格式不支持或执行失败
- **THEN** Verifier 不得将未完整扫描的内容视为无秘密
- **AND** 系统以明确的策略或验证非成功结果结束且不产生 `patch_ready`

### Requirement: 可交付 patch 必须由封存 Workspace 生成
系统 SHALL 从通过验证的冻结 Workspace 生成 canonical diff，并关联基线 commit、diff hash、Verifier 报告和有效策略版本。只有该完整工件集合存在时，运行才 SHALL 进入 `patch_ready`。

#### Scenario: 成功封存可交付 patch
- **WHEN** Workspace 通过策略与 Verifier 检查，且 canonical diff 与验证报告均已封存
- **THEN** 系统生成关联基线 commit 和工件哈希的 `patch_ready` 结果

#### Scenario: 封存期间基础设施失败
- **WHEN** diff、hash 或验证工件在封存期间缺失或上传失败
- **THEN** 系统标记 `infrastructure_error`
- **AND** 不得将任何部分产物标记为 `patch_ready`

### Requirement: 验证不充分必须与成功结果区分
系统 SHALL 将 `verification_inconclusive`、`baseline_broken`、`budget_exhausted`、`stale`、`no_progress` 和策略/基础设施失败与 `patch_ready` 区分。未完成验证的 patch 如被保留供参考 SHALL 显著标记为不可直接采用。

#### Scenario: 验证依赖不可用
- **WHEN** 必要验证因受管环境或依赖不可用而无法完成
- **THEN** 系统以 `verification_inconclusive` 或相应事实终态结束
- **AND** 不将该 patch 计为已验证成功
