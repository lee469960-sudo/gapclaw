## Purpose

定义与 production 完全隔离的 staging 发布拓扑，使受信任的不可变 manifest 能在远程 staging 主机完成可审计演练，而不扩大生产部署或 Agent 的权限边界。

## ADDED Requirements

### Requirement: Staging 发布链路与生产环境隔离

系统 SHALL 使用独立的 GitHub `staging` Environment、专用 self-hosted Runner 标签、主机目录、Compose 项目、Docker 网络、release-state 和 ACR pull-only 身份执行 staging 发布。staging 部署 MUST 仅消费受验证的 API/Web immutable digest manifest，MUST NOT 读取、写入、挂载或复用 production 的 Runner、状态、证书、凭据、域名或网络。

#### Scenario: 受保护 staging 部署
- **WHEN** 受验证的发布 manifest 被提交给 staging 部署链路
- **THEN** 只有通过 `staging` Environment 的专用 Runner 可以调用固定 staging Deploy Runner
- **AND** 该工作流不检出 tag 内容、不接收 production 或 staging 的 ACR pull 凭据

#### Scenario: 环境隔离被破坏
- **WHEN** staging 配置引用 production 的主机路径、Runner 标签、网络、证书、凭据或域名
- **THEN** 安装或配置验证失败
- **AND** 不启动 staging 发布或改变 production 状态

### Requirement: Staging 使用独立的浏览器、回调和私有控制端点

系统 SHALL 将 staging 浏览器入口固定为 `staging.gapclaw.online`，将 Runner 到 GAP 的 mTLS callback 入口固定为 `runner-staging.gapclaw.online`，并将 GAP 到 Runner 的控制地址固定为仅 staging API 可达的 `https://gap-runner-staging.internal:9443`。公网 callback 主机 MUST NOT 代理 Runner `/v1/*` 控制路径，私有控制地址 MUST NOT 暴露到公网。

上述浏览器与 callback 反向代理 SHALL 使用宿主机已安装的 Caddy 和受管 staging Caddyfile。系统 MUST NOT 启动 Nginx 容器、加载 Nginx 配置或复用 production 的 Caddy 配置、证书或站点定义。

#### Scenario: Staging 私有控制调用
- **WHEN** staging Release Management 查询 status/health 或提交受确认 rollback
- **THEN** 请求只通过 `gap-runner-staging.internal:9443` 的双向 mTLS 私有路径到达 staging Runner
- **AND** 请求不能到达 production Runner 或公网 callback 主机

#### Scenario: Staging 结果回传
- **WHEN** staging Runner 发送终态发布结果
- **THEN** 只有带受信任 staging 客户端证书的请求可通过 `https://runner-staging.gapclaw.online/internal/release-runner/callback`
- **AND** 回传不包含 ACR 凭据、证书材料、任意命令或 production 状态

#### Scenario: Caddy 代理配置隔离
- **WHEN** staging Caddyfile 被安装或校验
- **THEN** 它仅声明 staging 浏览器与 callback 主机，并仅将 callback 精确转发到 staging API
- **AND** Caddy 校验失败或存在 Nginx/production 代理引用时，不启动或重载 staging 代理

### Requirement: 发布管理配置按部署环境固定且不可由请求选择

系统 SHALL 在进程启动时从受管环境配置加载唯一的 deployment environment bundle，包括目标标识、私有 Runner URL、callback identity、CA 和客户端证书引用。浏览器、Release Agent、MCP、普通 API 请求或发布 manifest MUST NOT 选择、覆盖或切换部署环境。

#### Scenario: 启动 staging GAP
- **WHEN** staging GAP 使用受管 staging 配置启动
- **THEN** 发布管理只接受 staging 目标和 staging 私有 Runner URL
- **AND** API/UI 响应仅显示已脱敏的当前环境状态

#### Scenario: 请求跨环境操作
- **WHEN** 调用方提交 production 目标、Runner URL、镜像、digest 或环境标识以尝试影响 staging 发布管理
- **THEN** 系统在发送 Runner 请求前拒绝该操作
- **AND** 不改变任何 staging 或 production 发布状态

### Requirement: Staging 演练生成可审计上线前证据

系统 SHALL 提供一个受控 staging 演练流程，验证 digest 部署、Runner 独立 status/health、健康失败自动回滚或恢复、GAP 暂不可用时的回传重试，以及管理员确认 rollback 的审计闭环。演练记录 MUST 明确标识为 staging，且不得包含秘密材料。

#### Scenario: 完成 staging 发布演练
- **WHEN** 授权操作员在 staging 主机执行一次受保护发布和确认回滚演练
- **THEN** 系统记录 manifest、健康、回传重试、操作者和回滚结果的脱敏证据
- **AND** 证据证明 staging workflow 未执行 tag 内容且未暴露 ACR pull 凭据
