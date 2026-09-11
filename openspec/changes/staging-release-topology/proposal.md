## Why

现有轻量可信发布链路固定为 production，不能在不触及生产域名、回调入口、Runner 或凭据的情况下完成上线前演练。需要一个与生产隔离但复用同一不可变 manifest 和受限 Runner 边界的 staging 拓扑，才能在接入远程服务器后安全验证完整发布闭环。

## What Changes

- 新增 staging 发布拓扑：独立 GitHub `staging` Environment、self-hosted Runner 标签、主机目录、Compose 项目/网络、ACR pull-only 配置和本地状态。
- 为 staging 使用独立的 `staging.gapclaw.online` 浏览器域名、`runner-staging.gapclaw.online` mTLS callback 域名，以及仅 staging API 可访问的 `gap-runner-staging.internal:9443` 私有控制地址。
- 让受验证的 tag manifest 可由受保护的 staging workflow 部署到固定 staging Runner；staging 不接收生产 ACR 凭据、证书、状态文件或网络。
- 提供可审计 staging 演练流程，覆盖 digest 部署、健康、暂时回调失败、管理员确认回滚及 production 隔离检查。

## Capabilities

### New Capabilities

- `staging-release-topology`: 与 production 隔离的受保护 staging 发布、Runner、mTLS 和演练闭环契约。

### Modified Capabilities

- None. This capability depends on the unarchived `release-agent-lightweight-deploy` change; its main capability specs have not yet been synchronized.

## Impact

- 新增 staging GitHub workflow、Environment 文档、Runner/Caddy/Compose 资产和受控配置；宿主机已安装的 Caddy 承担浏览器与 mTLS callback 反向代理，不再运行 Nginx。
- API 的发布管理配置需按部署环境选择固定的私有 Runner 与 callback identity，且不能跨环境读取状态或凭据。
- 需要一台用户授权的远程 staging Linux 主机、staging DNS/TLS/mTLS 材料和 ACR pull-only 身份后才能执行真实演练。
