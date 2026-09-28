## MODIFIED Requirements

### Requirement: GAP 提供确定性的内置 Release Agent

系统 SHALL 提供名为「Release Agent」的内置系统 Agent，用于展示和解释 GAP 自身的发布状态、健康结果与历史记录。Release Agent MUST 以确定性发布状态机处理部署状态；它 MAY 基于已存档记录生成人类可读说明，但 MUST NOT 生成或执行任意主机命令、Docker 命令或 SSH 操作。发布管理界面 MUST render persisted release status and history independently from live Runner rollback-target probing, so a slow or unreachable Runner does not block the archived release view.

#### Scenario: 用户查看发布状态

- **WHEN** 已认证用户打开 Release Agent 或发布管理界面
- **THEN** 系统展示当前发布、最近状态、健康结果与可用历史记录
- **AND** 展示内容来源于已存档发布记录或 Runner 的受限状态接口

#### Scenario: Slow Runner does not block archived release view

- **WHEN** an administrator opens or refreshes the Release Management page while Runner status discovery is slow or unavailable
- **THEN** the page displays persisted current release and history without waiting for the Runner probe to finish
- **AND** the rollback card reports Runner rollback-target availability separately

#### Scenario: 请求任意部署命令

- **WHEN** 用户要求 Release Agent 执行任意 shell、Docker 或 SSH 命令
- **THEN** Release Agent 不执行该命令
- **AND** 仅返回其受限发布管理能力允许的状态或回滚操作说明

### Requirement: 管理员可受确认地回滚至 Runner 已知健康版本

系统 SHALL 仅向管理员显示并启用回滚控制。执行回滚前，界面 MUST 显示 Runner 当前可回滚的最近一次已知健康版本，并要求管理员明确确认该目标及确认短语。GAP MUST 仅经私有 `gap-runner.internal:9443` 以双向认证的受限 rollback 操作发送给 Runner；Runner MUST 仅回滚至其本地记录的已知健康版本，拒绝任意 tag、digest 或命令输入。Rollback-target discovery MUST use a short bounded read timeout and MUST fail closed as unavailable when the Runner cannot be reached quickly.

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
- **THEN** GAP 显示不可回滚状态
- **AND** 不允许管理员指定任意版本、tag、digest 或镜像

#### Scenario: Runner rollback-target probe times out

- **WHEN** rollback-target discovery exceeds the short read timeout or cannot connect to Runner
- **THEN** GAP returns rollback target unavailable
- **AND** the Release Management page remains usable for persisted status and history
