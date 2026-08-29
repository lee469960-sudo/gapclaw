# code-agent-coding-runtime Specification

## Purpose

定义 CodeAgent 如何在不重建 Task、Repository、Sandbox、Manifest、Skill、MCP、Verifier 与 Sealer 架构的前提下，选择并运行 Claude Code 作为受控 Coding Runtime。

## Requirements

### Requirement: CodeAgent 必须通过显式 Coding Runtime 运行编码循环

系统 SHALL 支持在 CodeAgent run 的冻结契约中显式选择 `coding_runtime=claude_code`。未显式选择该 runtime 时，系统 SHALL 保持现有 CodeAgent runtime 行为。选择 Claude Code 后，CodeAgent SHALL 只把代码理解、编辑、测试、调试和测试修复循环委托给 Claude Code；Task、Repository、Workspace、Sandbox、Manifest、Skill、MCP、Verifier、Sealer、状态与结果管理仍由现有 CodeAgent 控制。

#### Scenario: 默认 runtime 保持兼容

- **WHEN** CodeAgent run 未显式选择 `coding_runtime=claude_code`
- **THEN** 系统使用现有 CodeAgent runtime
- **AND** 不启动 Claude Code，不生成 Claude Code 专属配置，也不改变现有 run 终态语义

#### Scenario: 显式选择 Claude Code runtime

- **WHEN** CodeAgent run 的冻结契约选择 `coding_runtime=claude_code`
- **THEN** 系统在现有 run、Workspace、Sandbox 与 Manifest 边界内启动 Claude Code coding loop
- **AND** CodeAgent 不再对该 run 执行细粒度 coding planner/replanner/executor
- **AND** 最终任务成功仍不得由 Claude Code 文本声明决定

### Requirement: Coding Runtime Adapter 必须提供冻结输入与受限输出

系统 SHALL 通过单一 runtime adapter 启动 Claude Code。Adapter 输入 SHALL 来自冻结 run 契约，并至少包含 run 标识、任务契约、Workspace/repository 路径、允许路径、验证计划、有效策略、预算、绑定 Skill、授权 MCP、模型配置与 retry 上下文。Adapter 输出 SHALL 仅表达编码阶段事实，包括状态、退出码、摘要、transcript 路径、脱敏 transcript 路径、变更文件、工具审计、runtime events、预算用量、错误类型与错误摘要；不得直接输出 `patch_ready` 或绕过 Verifier 的成功结论。

#### Scenario: Adapter 使用冻结输入启动

- **WHEN** CodeAgent 将 run 交给 Claude Code runtime
- **THEN** Adapter 使用该 run 已冻结的任务、策略、预算、Skill/MCP 授权和 Workspace 路径启动
- **AND** runtime 执行期间不得隐式扩大允许路径、工具、网络、模型或 MCP 授权

#### Scenario: Adapter 返回编码阶段结果

- **WHEN** Claude Code coding loop 结束
- **THEN** Adapter 返回 coding completed、failed、timeout 或 unavailable 等编码阶段事实
- **AND** 不将 Claude Code 的退出码、最后回复或 done 文本映射为 `patch_ready`

### Requirement: Claude Code runtime 必须具备可观察 preflight 与失败分类

系统 SHALL 在每个 Claude Code run 进入 coding loop 前执行 runtime preflight，验证 Claude Code CLI 可用、配置目录可写、repository working directory 可读写、MCP 配置可解析、模型端点可连接且预算 watchdog 可用。preflight 或 runtime 失败 SHALL 产生稳定失败分类，至少区分 `runtime_unavailable`、`model_unavailable`、`mcp_config_failed`、`skill_load_failed`、`budget_exhausted`、`coding_failed`、`coding_timeout`、`verification_failed` 与 `infrastructure_error`。

#### Scenario: preflight 通过后启动 coding loop

- **WHEN** Claude Code CLI、run-local 配置、repository cwd、MCP 配置、模型连接和预算控制均通过 preflight
- **THEN** 系统允许进入 Claude Code coding loop
- **AND** 记录不含秘密的 preflight 事实和 runtime 版本信息

#### Scenario: preflight 失败

- **WHEN** 任一 preflight 检查失败
- **THEN** 系统不得启动 Claude Code coding loop
- **AND** run 进入对应稳定失败状态并提供可行动原因

### Requirement: Claude Code runtime 事件必须汇入 CodeAgent 统一事件流

系统 SHALL 将 Claude Code runtime 的关键过程映射为 CodeAgent 统一事件流中的稳定事件。事件至少 SHALL 包含 `runtime_started`、`skill_loaded`、`mcp_loaded`、`tool_call`、`file_changed`、`test_run`、`verifier_failed_retrying`、`verifier_passed` 与 `artifact_sealed`。用户可见事件、阶段代码片段和 transcript SHALL 经过脱敏、UTF-8 清洗与长度限制，不得直接透传未经处理的原始输出；Claude `stream-json` 的 `system/init` 等协议元数据 SHALL 不得作为用户摘要展示。

#### Scenario: 用户查看运行过程

- **WHEN** 使用 Claude Code runtime 的 CodeAgent run 正在执行或已结束
- **THEN** 用户可在现有 CodeAgent 对话/结果界面看到 runtime 启动、Skill/MCP 加载、工具调用、文件变化、测试、Verifier retry 和 artifact 封存状态
- **AND** 事件内容不暴露秘密值或未授权配置

#### Scenario: runtime 输出包含协议初始化记录

- **WHEN** Claude `stream-json` 输出 `system/init` 或其他协议生命周期记录
- **THEN** 系统不得将该记录作为对话摘要或最终任务结果展示
- **AND** 用户可见摘要仅保留 assistant 文本、工具结果或明确的运行失败说明

#### Scenario: runtime 原始输出包含敏感样式内容

- **WHEN** Claude Code 输出或工具日志中包含 secret-like 内容
- **THEN** 事件管道和 transcript 保存路径 SHALL 对用户可见内容执行脱敏
- **AND** patch、source 与 artifact 仍由现有扫描和 Verifier 链路独立检查

### Requirement: Claude Code session 只能在同一 run 内复用

系统 SHALL 在同一个 CodeAgent run 的 verifier retry 期间复用 Claude Code session，以保留编码上下文；跨 run、跨项目或跨 Manifest 版本 MUST NOT 复用 Claude Code session。

#### Scenario: Verifier retry 复用 session

- **WHEN** 同一 run 的 Claude Code coding completed 后 Verifier 失败且仍有 retry 预算
- **THEN** 系统向同一 Claude Code session 注入脱敏 Verifier feedback 并继续修复
- **AND** retry 过程继续受同一冻结策略、Workspace、预算和审计边界约束

#### Scenario: 新 run 不复用旧 session

- **WHEN** 创建新的 CodeAgent run
- **THEN** 系统创建新的 Claude Code session
- **AND** 不从其他 run 继承对话、工具状态、临时配置或未封存文件

### Requirement: Claude Code 目标定位失败必须以 target_not_found 退出

当 `claude_code` run 的任务要求替换或修改明确目标值，且 runtime 在允许范围内无法找到该目标值时，系统 SHALL 以公开非成功终态 `target_not_found` 结束该 run。该终态 MUST 不被映射为基础设施失败、模型失败、Verifier 失败或 `no_change_justified`。`target_not_found` MUST NOT 生成 patch 或 sealed artifact，并 SHALL 向用户展示被搜索的目标、搜索范围和不含秘密的定位摘要。

#### Scenario: 旧 IP 未找到

- **WHEN** 用户要求将 `192.168.10.36:5432` 替换为 `192.168.0.105:5432`
- **AND** Claude Code 在冻结允许路径内未找到旧目标值
- **THEN** run 以 `target_not_found` 结束
- **AND** 结果说明未找到 `192.168.10.36:5432`
- **AND** 不生成业务文件 patch

### Requirement: Claude Code 多候选或范围不清必须以 needs_user_decision 退出

当 `claude_code` run 发现多个可能目标，且自动选择会改变环境、profile、dev/local/prod 边界或其他高风险配置语义时，系统 SHALL 以公开非成功终态 `needs_user_decision` 结束该 run。系统 SHALL 列出候选文件、命中摘要和建议问题，并保留不可执行 Workspace 到正常保留期。系统 MUST NOT 自动选择候选、批量修改所有候选或保持 runner 长时间等待用户输入。

#### Scenario: 多个 local/profile/dev 候选

- **WHEN** 任务定位到多个可能的 local/profile/dev 配置文件或命中项
- **AND** 无法从任务契约确定唯一目标
- **THEN** run 以 `needs_user_decision` 结束
- **AND** 结果列出候选文件与不含秘密的命中摘要
- **AND** runner 被停止，Workspace 按保留策略进入不可执行状态

### Requirement: Claude Code 任务结果必须区分平台验收和业务目标结果

系统 SHALL 区分平台链路是否可用与具体业务目标是否完成。若 Claude Code 成功启动、加载授权 Skill 并进入 coding loop，但业务目标值不存在，平台验收 MAY 视为链路正确，业务 run 仍 SHALL 以 `target_not_found` 结束。

#### Scenario: 平台链路正常但业务目标不存在

- **WHEN** Claude Code preflight 通过并进入 coding loop
- **AND** 任务目标值不存在于允许范围
- **THEN** 平台验收可记录 runtime 链路已正常工作
- **AND** 该业务 run 的用户可见终态仍为 `target_not_found`
