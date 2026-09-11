## Context

`release-agent-lightweight-deploy` 建立了 production-only 的 digest 发布、Runner、mTLS callback 与 Release Management 边界，但真实非生产演练缺少隔离的主机和配置。此变更引入一套并行 staging 拓扑，而不是让生产控制面接受请求级环境切换。

## Goals / Non-Goals

**Goals:**

- 让一台授权的远程 Linux staging 主机可安全运行完整发布与回滚演练。
- 保持 staging 与 production 的 Runner、状态、Docker 网络、DNS/TLS/mTLS、ACR pull-only 身份和 GitHub Environment 隔离。
- 使 staging 仍只消费 manifest digest，且 Release Agent 维持无 shell/Docker/MCP 权限。

**Non-Goals:**

- 不将同一台主机或同一套凭据同时作为 production 与 staging。
- 不在浏览器、API、manifest 或 Agent 提供环境选择、任意 URL、镜像或命令输入。
- 不引入镜像签名、蓝绿、任意部署脚本或生产实际发布。

## Decisions

### 1. 使用并行固定 staging 命名空间

staging 使用 `staging.gapclaw.online`、`runner-staging.gapclaw.online`、`gap-runner-staging.internal:9443`、`gap-staging` Compose 项目和 `/opt/gap-staging-runner`、`/opt/gap-staging` 主机目录。GitHub 使用独立 `staging` Environment 和 `[self-hosted, linux, staging]` Runner 标签。

这比在 production 配置上加入运行时 selector 更简单且可审计：每个部署的进程只持有一个不可变环境 bundle。共享域名、状态路径或 Docker network 会产生交叉路由与凭据泄露风险，因此禁止。

### 2. 保持控制面双向路径分离

staging API 仅通过 host-gateway 和 `gap-runner-staging.internal` 访问 Runner 的 mTLS `/v1/*`。宿主机已安装的 staging Caddy 仅在 `runner-staging.gapclaw.online` 接收 Runner callback；不会代理 `/v1/*`。证书 SAN、CA、客户端身份都为 staging 专用，不能与 production 共用。Caddy 以受管 Caddyfile 运行，不启动 Nginx 容器或复用 production 的 Caddy 配置。

### 3. 复用 manifest 契约，不复用主机执行资产

staging workflow 下载同一不可变 manifest 产物，但只允许 target `staging` 并调用 staging 主机上的固定 Runner binary。Runner 发布包必须提供实际 CLI/server entrypoint、固定 Compose 模板、systemd unit 和安装验证；它不从 Git checkout 或 tag 中读取部署资产。

这复用已验证的 digest contract，同时让实际主机安装可被演练。替代方案是让 production Runner 模拟 staging；它会混合状态、权限与网络边界，故拒绝。

### 4. 演练将故障与恢复作为显式阶段

演练先建立健康基线，随后记录一次健康成功、一次受控的 GAP callback 暂不可用/重试和一次管理员确认 rollback。由 staging Runner 生成的状态、日志和 GAP 审计共同组成证据；任何对账不一致保持 `reconciliation_required`，不自动继续。

## Risks / Trade-offs

- [新增 staging 证书与 DNS 运维] → 使用独立 staging CA/identity，文档化轮换并在安装校验中拒绝 production 路径。
- [一台远程主机配置错误] → 在 GitHub workflow、Runner installer、Compose 和 API bundle 中重复环境名/路径/域名的 fail-closed 校验。
- [staging 演练需要 ACR 镜像] → 使用 staging 专用 pull-only identity；不把它注入 GitHub workflow 或审计记录。
- [Runner 发布包可能与测试模型脱节] → 将实际 CLI/server entrypoint、资产安装和 staging smoke contract 纳入任务与验收。

## Migration Plan

1. 在代码库中增加 staging 固定配置、workflow 和受管 Runner 发行/安装资产及隔离测试。
2. 配置 GitHub `staging` Environment、DNS、staging CA/证书、ACR pull-only identity 和一台独立远程 Linux 主机。
3. 在该主机安装受管 staging Caddyfile、Runner 和 GAP Compose，验证只有 staging endpoint 可达。
4. 执行受控 staging 演练并保存脱敏证据；失败时停用 staging workflow、保留本地状态以便诊断，不影响 production。
