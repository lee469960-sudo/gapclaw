# agent-response-policy Specification

## Purpose

为不同 Agent 提供可审计的回复风格和质量策略配置，使内容型 Agent 能默认输出结构化结果，同时保留简单对话的自适应和低延迟行为。

## Requirements

### Requirement: Agent 可配置回复风格

系统 SHALL 支持 `adaptive`、`concise`、`structured` 和 `analytical` 回复风格。未配置的 Agent MUST 使用 `adaptive`；配置无效值时 MUST 回退到 `adaptive`，不得阻止普通对话。

#### Scenario: 得到大脑使用结构化风格

- **WHEN** 管理者将内容型 Agent 的回复风格设置为 `structured` 并保存
- **THEN** 后续复杂普通问题使用结构化回复约束
- **AND** 简单问候仍可自然简短回答

#### Scenario: 无效风格安全回退

- **WHEN** 客户端提交不存在的回复风格
- **THEN** API 拒绝无效值或规范化为 `adaptive`
- **AND** Agent 仍可继续处理普通对话

### Requirement: 回复策略可被运行指标审计

系统 SHALL 在终态指标中记录实际使用的回复风格、是否执行质量检查、是否执行补全及最终质量状态。记录 MUST 不包含完整提示词、凭据或未选择工具目录。

#### Scenario: 结构化补全产生审计记录

- **WHEN** 结构化风格的复杂问题触发一次质量补全
- **THEN** 运行指标包含风格、补全次数、触发原因和最终状态
- **AND** 指标不泄露用户敏感内容
