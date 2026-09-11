## Why

当前 Git tag 发布会在生产 self-hosted runner 上检出并执行标签内的部署脚本，同时把 ACR 凭据注入 GitHub Actions。该模式无法把构建产物、主机部署权限和可审计回滚边界可靠地分开，也没有可由 GAP 统一查看的发布状态与受控回滚入口。

本变更以轻量可信为目标，复用现有 GitHub 构建与 ACR 镜像发布，不引入镜像签名基础设施；通过固定主机 Deploy Runner、受保护的默认分支部署工作流和 Release Agent，完成 GAP 自身的自动部署、健康判定、失败回滚与审计闭环。

## What Changes

- 新增主机侧 Deploy Runner：仅执行固定的 GAP 部署、健康、状态和回滚操作，使用主机受管目录中的脚本与 Compose 模板，而非 Git tag 中的可执行文件。
- 将生产部署从 tag 所在的工作流与检出脚本中移出，改为默认分支受保护的部署工作流；部署输入为经构建完成后解析并记录的 API/Web ACR 不可变 digest。
- 部署前强制校验 Git tag 与 `deploy/gap.version` 完全一致；生产主机仅从 `/opt/gap/.env` 读取 ACR 拉取凭据，GitHub 部署任务不再接触该凭据。
- 新增本地持久化 release-state，按现有容器健康与 API `/health` 判定成功；失败时自动回滚到最近一次已知健康版本，并保留最近五个成功发布记录。
- 新增主机级 Caddy 入口：它独占 80/443，以 `gapclaw.online` 提供 GAP Web/API，并以受客户端证书验证的 `runner.gapclaw.online` 接收固定 Runner 回调；后续 Compose 栈仅通过同一入口的独立域名/子域名接入，不自行绑定公网端口。
- 新增 GAP 内置系统 Agent「Release Agent」及发布管理 API/UI：通过仅 GAP API 可达的私有 `gap-runner.internal:9443` 双向 mTLS 获取 Runner 状态，并通过公网 mTLS 回调接收结果回传，展示发布审计；仅管理员在明确确认后可回滚到已知健康版本。
- 明确 Release Agent 为确定性发布状态机：它可解释发布记录，但不生成或执行任意主机命令，也不拥有部署用 Docker 或主机 SSH 权限；既有 Code Agent Docker 运行时不在本变更范围内。

## Capabilities

### New Capabilities

- `trusted-gap-deployment`: 受保护工作流、固定主机 Deploy Runner、镜像 digest 部署、健康检查、自动回滚及状态持久化的轻量可信 GAP 自部署契约。
- `release-management`: 内置 Release Agent 对发布状态的受控可观测、审计和管理员回滚管理契约。

### Modified Capabilities

- None.

## Impact

- GitHub Actions 发布工作流及其部署触发方式。
- 新增主机受管的 Deploy Runner 服务、固定 Compose 模板、systemd 安装/运维材料和本地状态目录。
- 新增受管 Caddy site、TLS/mTLS 配置与主机 loopback 上游；GAP 服务只保留主机本地端口，不加入代理容器网络。
- 现有 `scripts/deploy.sh`、`deploy/docker-compose.prod.yml` 与部署文档将迁移为 Runner 管理的生产部署资产或相应的开发参考。
- API 的 Release Agent、发布记录/回调接口、授权控制和 Web 发布管理界面。
- 生产环境配置：主机 ACR pull-only 凭据、双向 mTLS 证书与受保护 GitHub Environment/默认分支规则。
- 既有 Code Agent Docker socket 挂载保持其现有行为；本变更隔离 Release Agent 的部署接口，但不将该历史运行时权限迁移或移除。
