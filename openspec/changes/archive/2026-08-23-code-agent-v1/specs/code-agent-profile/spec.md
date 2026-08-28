## Purpose

定义 Code Profile 在统一 Agent 平台中的显式选择、最小任务契约与受限编码能力，同时确保默认 Standard Agent 的行为不发生变化。

## ADDED Requirements

### Requirement: Code Profile 必须显式选择且 Standard 默认兼容
系统 SHALL 仅在 Agent 或任务显式选择 `code` Profile 时启用 CodeAgent 行为；缺失 Profile 或选择 `standard` 时 SHALL 保持既有 Standard Agent 的工具、运行语义和终态。Code Profile SHALL 复用统一任务身份、权限、预算、事件和审计契约。

#### Scenario: 未声明 Profile 的既有任务
- **WHEN** 创建或运行一个未声明 Profile 的既有任务
- **THEN** 系统按 Standard Profile 执行
- **AND** 不准备 Workspace、不暴露 Code Tools、也不产生 CodeAgent 专属终态

#### Scenario: 显式选择 Code Profile
- **WHEN** 获授权的任务显式选择 `code` Profile
- **THEN** 系统按该项目的 Code 策略准备 CodeAgent 执行上下文
- **AND** 该运行继续使用统一任务身份、预算、事件与审计记录

### Requirement: CodeAgent 任务必须具有冻结的最小契约
修改型 CodeAgent 任务 SHALL 在执行前冻结仓库、基线 commit、目标描述、允许修改范围、验证策略和预算。缺少任一必填项的任务 SHALL 不得进入写入阶段，并 SHALL 返回可行动的澄清或策略拒绝结果。

#### Scenario: 完整契约启动修改任务
- **WHEN** CodeAgent 修改任务提供了受管仓库、基线 commit、目标、允许路径、验证策略和预算
- **THEN** 系统冻结该契约并允许其进入准备阶段

#### Scenario: 缺少验收或允许范围
- **WHEN** CodeAgent 修改任务缺少验证策略或允许修改范围
- **THEN** 系统不得创建可写执行上下文
- **AND** 返回指出缺失字段的可行动结果

### Requirement: Code Profile 仅暴露受策略约束的 V1 代码能力
Code Profile SHALL 提供受项目策略约束的读取、搜索、编辑/patch、测试和受限 shell 能力。V1 SHALL 不提供自动 Git 提交、推送、创建 PR、真实秘密访问、任意网络访问、动态工具加载或 Agent 间自动委派。

#### Scenario: 允许的代码修改工具
- **WHEN** CodeAgent 调用项目策略允许的读取、搜索、编辑或测试能力
- **THEN** 系统在有效 Workspace 和能力范围内执行该调用并记录审计事件

#### Scenario: 请求 V1 非目标能力
- **WHEN** CodeAgent 请求推送代码、读取真实秘密、访问未批准网络或动态加载工具
- **THEN** 系统拒绝该动作
- **AND** 将该拒绝记录为策略事件而不执行动作
