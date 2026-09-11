## Purpose

定义 GAP 内置 Release Agent 对自身发布状态的可观测、审计和受控回滚能力，同时确保主机部署权限不进入通用 Agent 运行时。

## ADDED Requirements

### Requirement: GAP 提供确定性的内置 Release Agent
系统 SHALL 提供名为「Release Agent」的内置系统 Agent，用于展示和解释 GAP 自身的发布状态、健康结果与历史记录。Release Agent MUST 以确定性发布状态机处理部署状态；它 MAY 基于已存档记录生成人类可读说明，但 MUST NOT 生成或执行任意主机命令、Docker 命令或 SSH 操作。

#### Scenario: 用户查看发布状态
- **WHEN** 已认证用户打开 Release Agent 或发布管理界面
- **THEN** 系统展示当前发布、最近状态、健康结果与可用历史记录
- **AND** 展示内容来源于已存档发布记录或 Runner 的受限状态接口

#### Scenario: 请求任意部署命令
- **WHEN** 用户要求 Release Agent 执行任意 shell、Docker 或 SSH 命令
- **THEN** Release Agent 不执行该命令
- **AND** 仅返回其受限发布管理能力允许的状态或回滚操作说明

### Requirement: Release Intake 仅接收验证后的固定 CI 发布任务
GitHub CI 的固定签名发布 Hook MUST 由非 LLM 的 Release Intake 服务处理。GAP SHALL 仅在 HMAC-SHA256 签名、时间窗口、delivery id、目标和完整不可变 manifest 均通过验证后创建发布 intake 记录并调用私网 Runner deploy。Release Agent 仅可读取该服务记录的 intake、部署和回传审计，且不得选择 Hook URL、目标、镜像、命令或签名材料。

#### Scenario: 重复或无效 Hook
- **WHEN** Hook 签名、时间窗口、delivery id 或 manifest 校验失败，或 delivery id 已被接受
- **THEN** GAP 不调用 Runner 且不创建第二次部署
- **AND** Release Agent 仅展示已存档的安全 intake 结果

### Requirement: 发布记录在 GAP 中可审计且与 Runner 状态对账
系统 SHALL 持久化来自受信任 Runner 的发布生命周期记录，至少包含发布清单标识、目标标识、状态变迁、API/Web digest、开始与结束时间、健康结果、自动回滚结果、触发来源和失败摘要。Release Agent MUST 能标识 Runner 本地状态与 GAP 审计记录之间的未同步或不一致情形，不得假定中断部署已成功或盲目恢复。

#### Scenario: Runner 回传成功发布
- **WHEN** 受信任 Runner 回传一次成功发布结果
- **THEN** GAP 保存可审计记录并在 Release Agent 界面显示该结果

#### Scenario: 中断发布需要对账
- **WHEN** GAP 发现 Runner 当前状态与其最后一条审计记录不一致，或发布处于未终态
- **THEN** Release Agent 显示需要对账的状态
- **AND** 不自动重新执行或继续该发布

### Requirement: 管理员可受确认地回滚至 Runner 已知健康版本
系统 SHALL 仅向管理员显示并启用回滚控制。执行回滚前，界面 MUST 显示 Runner 当前可回滚的最近一次已知健康版本，并要求管理员明确确认该目标及确认短语。GAP MUST 仅经私有 `gap-runner.internal:9443` 以双向认证的受限 rollback 操作发送给 Runner；Runner MUST 仅回滚至其本地记录的已知健康版本，拒绝任意 tag、digest 或命令输入。

#### Scenario: 管理员确认回滚
- **WHEN** 管理员选择界面显示的已知健康版本并输入要求的确认短语
- **THEN** GAP 创建包含操作者、目标、时间与结果的回滚审计记录
- **AND** 向 Runner 发起受限回滚请求

#### Scenario: 非管理员或确认不完整
- **WHEN** 非管理员尝试回滚，或管理员未正确确认显示的目标与确认短语
- **THEN** 系统拒绝回滚
- **AND** 不向 Runner 发送回滚请求

#### Scenario: Runner 不存在已知健康版本
- **WHEN** 管理员请求回滚但 Runner 没有可用的最近一次已知健康版本
- **THEN** 系统显示不可回滚原因
- **AND** 不接受任意用户提供的 tag 或 digest 作为替代目标

### Requirement: 发布管理接口不扩张 Agent 或应用的主机权限
发布管理 API 和 UI MUST 仅暴露发布状态、健康、审计和受控回滚所需的信息与操作。系统 MUST 不通过 Agent 配置、MCP、通用工具调用或发布管理接口向 Release Agent 授予 Docker、主机 shell、SSH 或 ACR 写入凭据。既有 Code Agent Docker 运行时不在本变更范围内，且 MUST NOT 成为 Release Agent 的部署执行通道。

#### Scenario: 常规 Agent 运行时
- **WHEN** 标准 ReAct Agent 或 Code Agent 运行任务
- **THEN** 它们不因 Release Agent 的存在而获得 Deploy Runner 或主机部署权限

#### Scenario: 发布记录包含敏感配置
- **WHEN** Runner 或 GAP 处理含有 ACR 凭据、证书材料或应用秘密的配置
- **THEN** 发布管理 API、界面和审计记录不返回或记录这些敏感值
