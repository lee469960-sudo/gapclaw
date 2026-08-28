## Purpose

定义 CodeAgent 对远程 Git 与本地只读仓库的受控接入、不可变解析和安全快照边界，使运行无需持有仓库凭据或直接访问原始代码源。

## ADDED Requirements

### Requirement: 仓库来源必须通过受控类型与 allowlist 接入

系统 SHALL 仅接受 `https`、`ssh`、管理员为精确主机与端口显式允许的内部 `http` Git 来源，或位于管理员配置只读根目录内的本地仓库。系统 MUST 拒绝嵌入凭据的 URL、`file://` 来源、未获允许的主机或端口、任意宿主路径，以及经规范化或符号链接解析后逃逸本地允许根目录的来源。

#### Scenario: 导入 allowlist 内的远程仓库

- **WHEN** 项目配置使用已批准协议、精确主机与端口的远程 Git 来源
- **THEN** 系统允许受控 Repository Importer 尝试解析和导入该来源
- **AND** 除 Importer 外的控制面或 runner 不直接获取该仓库

#### Scenario: 拒绝嵌入凭据或未允许来源

- **WHEN** 仓库 URL 包含用户名或密码、使用 `file://`、或其主机与端口不在有效 allowlist 中
- **THEN** 系统以稳定的 `repository_source_not_allowed` 原因拒绝保存、发布或导入
- **AND** 不向该来源发起请求

#### Scenario: 拒绝本地路径逃逸

- **WHEN** 本地仓库路径不在管理员只读根目录内，或经路径规范化或符号链接解析后逃逸该根目录
- **THEN** 系统拒绝导入并记录路径策略事件
- **AND** 不读取目标路径内容

### Requirement: 私有仓库认证必须使用不泄露的 Secret 引用

私有远程来源 SHALL 仅保存 Secret Store 中只读 Deploy Token 或等价只读凭据的引用。凭据值 SHALL 仅在 Importer 认证期间短暂可用，MUST NOT 出现在仓库 URL、Project 或 Manifest 数据、日志、审计 payload、错误消息、模型上下文、runner 容器、Workspace 或交付工件中。

#### Scenario: 使用 Secret 引用导入私有仓库

- **WHEN** 获授权项目引用有效的只读仓库凭据且来源通过策略检查
- **THEN** Importer 使用解析出的凭据完成认证并在结束后撤销其可用性
- **AND** 持久化的项目、Manifest 与快照元数据仅包含 Secret 标识符而不包含秘密值

#### Scenario: 仓库认证失败

- **WHEN** Secret 引用缺失、已停用、无权使用或远程服务拒绝该凭据
- **THEN** 系统以稳定的 `repository_auth_failed` 结果拒绝发布或导入
- **AND** 返回信息不得包含凭据值或可用于重放认证的内容

### Requirement: 发布时必须冻结不可变源码快照

系统 SHALL 在 Manifest 发布时解析配置的 branch、tag 或 commit ref，验证目标对象可用，并冻结精确 commit SHA 与不可变源码快照标识。Code run SHALL 仅使用已冻结快照；发布后的 branch 或 tag 漂移不得改变既有 Manifest，且 run 不得在准备或执行期间访问 Git 服务。

#### Scenario: 发布移动分支

- **WHEN** 管理者发布引用远程 branch 的有效 Manifest
- **THEN** 系统解析并记录当时的精确 commit SHA 与快照标识
- **AND** branch 后续移动不会改变该已发布版本启动的 run 基线

#### Scenario: ref 无法解析

- **WHEN** 配置的 branch、tag 或 commit 不存在、不可达或无法在限制时间内解析
- **THEN** 系统拒绝发布并返回 `repository_ref_invalid` 或 `repository_unreachable`
- **AND** 不创建可供 run 使用的部分源码快照

#### Scenario: run 期间 Git 服务不可用

- **WHEN** 已发布 Manifest 的源码快照有效但原 Git 服务在 run 期间不可达
- **THEN** run 仍从冻结快照准备 Workspace
- **AND** runner 不接收仓库凭据或 Git 服务网络权限

### Requirement: Repository Importer 必须阻止网络与内容边界逃逸

Importer SHALL 在每次连接及重定向前校验协议、精确主机与端口、DNS 解析结果和目标 IP；除被管理员明确批准的内部地址外，系统 MUST 拒绝 loopback、link-local、metadata、私有或其他未授权网段，并 MUST 拒绝跳出 allowlist 的重定向。Importer SHALL 禁用 Git hooks，V1 SHALL 拒绝 submodule 与 Git LFS，并 SHALL 对下载字节数、解包大小、文件数、单文件大小和耗时执行平台上限。

#### Scenario: DNS 或重定向指向未授权地址

- **WHEN** allowlist 主机解析到未获批准的地址，或任一重定向离开允许的协议、主机、端口或网段
- **THEN** Importer 在连接目标或继续重定向前拒绝操作
- **AND** 记录 `repository_network_policy_denied` 而不暴露敏感网络信息

#### Scenario: 仓库使用 V1 不支持的对象

- **WHEN** 待导入 commit 包含 submodule 声明或依赖 Git LFS 对象
- **THEN** 系统以 `repository_feature_unsupported` 拒绝发布
- **AND** 不递归获取其他仓库或 LFS 内容

#### Scenario: 仓库超过导入限制

- **WHEN** 仓库下载、解包、文件或时间消耗超过任一平台上限
- **THEN** 系统停止导入并返回 `repository_limit_exceeded`
- **AND** 清理未完成快照且不得将其用于 run

### Requirement: 源码快照不得授予原仓库写入能力

冻结快照和由其准备的 Workspace SHALL 与原仓库写权限隔离。系统 MUST NOT 自动 commit、push、创建 PR 或将 Workspace 写回远程或本地原仓库。

#### Scenario: 任务请求推送修改

- **WHEN** CodeAgent 或用户任务请求 commit、push、创建 PR 或写回本地来源
- **THEN** 系统拒绝该动作并记录策略事件
- **AND** 原仓库状态保持不变
