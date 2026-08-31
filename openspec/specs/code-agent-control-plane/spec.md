# code-agent-control-plane Specification

## Purpose

定义 Code Project 与版本化 Manifest 的可操作控制面，使管理员可在不直接操作数据库的前提下安全接入、发布和治理 CodeAgent 项目。

## Requirements

### Requirement: Code Project 可被受权限保护地管理

系统 SHALL 允许具备项目管理权限的用户创建、查看、更新、启用或停用 Code Project，并配置项目名称、描述、环境 tier 与访问主体。项目列表和详情 SHALL 仅向具有该项目访问权的用户展示；项目停用后不得再被用于新 Code run。

#### Scenario: 管理员创建内部非生产项目

- **WHEN** 具备项目管理权限的用户提交一个名称唯一、环境 tier 为 `internal_non_production` 的项目
- **THEN** 系统创建该项目并保存其访问控制配置
- **AND** 该项目可作为后续 Manifest 和 Code Profile 配置的候选项

#### Scenario: 无权用户不能管理项目

- **WHEN** 不具备项目管理权限或项目访问权的用户读取或修改项目
- **THEN** 系统拒绝该操作
- **AND** 不泄露未授权项目的配置、仓库或 Manifest 内容

#### Scenario: 停用项目阻止新运行

- **WHEN** 管理员停用一个 Code Project
- **THEN** 系统将其从可绑定项目候选项中移除
- **AND** 对该项目的新 Code run 返回可行动的项目不可用原因

### Requirement: Manifest 以草稿和已发布版本管理

系统 SHALL 允许项目管理者为 Code Project 创建和编辑草稿 Manifest，并在仓库来源、凭据引用和请求 Ref 字段校验通过后发布版本。Manifest SHALL 仅表达受管仓库来源类型与标识、可选 Git Secret 引用和请求的 branch/tag/commit；发布时 MUST NOT 导入仓库、扫描源码或封存源码快照。历史已发布版本 SHALL 可查看但不可被修改，且 MUST NOT 保存秘密值。运行 Sandbox、模型、Skill、MCP、策略、路径、验证计划、镜像和预算 MUST NOT 由 Manifest 配置。

#### Scenario: 发布有效 Manifest
- **WHEN** 管理者提交来源、凭据引用和请求 ref 字段有效的草稿
- **THEN** 系统发布仅包含 Git 配置的新版本
- **AND** 后续任务从 CodeAgent 编辑页资源配置获取运行环境
- **AND** 后续任务在绑定 Sandbox Workspace 内解析实际 commit

#### Scenario: 保存未完成草稿
- **WHEN** 管理者保存缺少 Git 必填信息的草稿
- **THEN** 系统保存草稿并标出 Git 字段问题
- **AND** 该草稿不得用于新任务

#### Scenario: 发布缺少 Git 信息的 Manifest 被拒绝
- **WHEN** 管理者尝试发布缺少仓库、ref 或可用凭据的草稿
- **THEN** 系统拒绝发布并返回字段级原因
- **AND** 不要求填写镜像、预算、验证计划或发布配置

#### Scenario: 发布无效 Manifest 被拒绝
- **WHEN** 仓库来源、凭据引用或 Ref 字段无法校验
- **THEN** 系统拒绝发布并保留当前已发布版本
- **AND** 返回可行动的 Git 字段原因

### Requirement: 控制面展示项目可用性和运行前门禁

系统 SHALL 向有权用户展示每个项目是否具备可用于新 Code run 的已发布 Manifest，以及不可用时的稳定原因。该状态 SHALL 区分项目停用、未发布 Manifest、Manifest 无效、环境 tier 不支持、仓库来源未批准、凭据引用不可用、ref 或快照无效、runner 镜像不可用或 digest 漂移，以及部署 Workspace mount 未就绪。

#### Scenario: 项目已就绪

- **WHEN** 项目已启用、环境 tier 受支持、具有安全来源的有效已发布 Manifest、冻结快照与可信 runner 均可用且部署门禁通过
- **THEN** 控制面将其标记为可用于新 Code run

#### Scenario: 项目未就绪

- **WHEN** 项目因停用、未发布或无效 Manifest、不支持环境 tier、来源/凭据/ref/快照问题、镜像问题或 Workspace mount 配置问题而不可用
- **THEN** 控制面显示对应的稳定不可用原因和下一步操作指引
- **AND** 不向无权用户泄露仓库、凭据或部署路径细节

### Requirement: 敏感 CodeAgent 控制必须按角色授权

系统 SHALL 仅允许平台管理员管理仓库来源 allowlist、本地只读根目录、Secret 引用可用性和可信 runner 镜像；仅允许有权项目负责人管理 Project 与 Manifest；仅允许有权 operator 启动或取消 run；仅允许有权 reviewer 查看受保护工件和验证报告。权限判断 SHALL 同时满足主体、组织、项目和资源范围，且不得因拥有较低层角色而提升到平台管理权限。

#### Scenario: 项目负责人引用已批准配置

- **WHEN** 有权项目负责人配置 Manifest
- **THEN** 系统允许其选择管理员已批准且对该项目可见的来源、Secret 引用和镜像
- **AND** 不允许其创建或放宽平台 allowlist、读取秘密值或批准新镜像

#### Scenario: operator 尝试修改敏感配置

- **WHEN** 仅具有 run 操作权限的主体尝试修改来源 allowlist、Secret 引用或可信镜像
- **THEN** 系统拒绝操作并记录授权失败
- **AND** 不泄露现有敏感配置的秘密内容

### Requirement: Manifest 必须声明 local 发布命令与确认策略

Manifest SHALL NOT 声明或消费 local 发布命令、target、网络目的地、Secret 引用、验证计划或并发锁。启用 local 发布时，控制面 MUST 忽略历史数据库列中的上述字段，且不得把它们纳入新 Run 契约。

#### Scenario: 发布有效 Manifest

- **WHEN** 已发布 Manifest 仅包含 Git 基线与既有非发布字段
- **THEN** 控制面允许创建普通 CodeAgent Run，包括用户随后提出的 local 发布任务
- **AND** 不把历史 `local_publish_*` 列写入冻结契约

#### Scenario: 发布配置缺失

- **WHEN** Manifest 不含 local 发布命令或确认策略
- **THEN** 控制面不得因此拒绝创建 Run
- **AND** 不得要求用户在 Manifest UI 补齐发布字段
