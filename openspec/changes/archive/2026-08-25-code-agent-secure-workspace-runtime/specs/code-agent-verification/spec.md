## MODIFIED Requirements

### Requirement: 修改结果必须经独立 Verifier 裁决

CodeAgent 修改任务 SHALL 在创建可写 runner 前对导入源码执行完整秘密扫描，并在交付前对 canonical diff 和所有待封存新增内容再次执行完整秘密扫描及任务契约和项目策略指定的 Verifier。Verifier SHALL 依据确定性执行事实裁决验证，不得由模型的“测试已通过”文本替代；秘密命中、扫描截断/不支持/失败、策略、路径或测试完整性检查失败时 SHALL fail closed 并阻止执行或成功交付。

#### Scenario: 导入源码命中秘密

- **WHEN** 冻结源码快照在创建可写 runner 前的扫描中命中秘密
- **THEN** 系统以明确安全结果结束准备
- **AND** 不创建可写 runner、不向模型暴露命中内容

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
- **AND** 系统以明确的策略或验证非成功结果结束，且不得进入可写执行或产生 `patch_ready`

#### Scenario: patch 扫描命中秘密

- **WHEN** canonical diff 或待封存新增内容在交付前扫描中命中秘密
- **THEN** 系统阻止 `patch_ready` 并记录脱敏安全事实
- **AND** 不在用户可见输出、日志或工件元数据中暴露秘密值
