## 1. 固定 staging 发布与环境配置

- [x] 1.1 为受验证 manifest 增加受控 staging target 支持，并以单元测试验证 production/staging target 只能被各自固定 Runner 接受，跨环境 target 在 Compose 前被拒绝。
- [x] 1.2 增加默认分支受保护的 staging 部署 workflow，使用 `staging` Environment 与 `[self-hosted, linux, staging]` 标签，只下载 manifest 并调用固定 staging Runner；以工作流结构测试验证不 checkout tag、不执行仓库部署资产且不传递 ACR 凭据。
- [x] 1.3 实现进程启动时固定的 staging Release Management environment bundle（target、私有 Runner URL、callback identity 和 mTLS 引用），并以 API 测试验证请求/浏览器/manifest 不能选择或覆盖环境且响应不暴露秘密。

## 2. Staging 主机、Runner 与网络隔离

- [x] 2.1 提供实际可安装的 Deploy Runner CLI/server entrypoint 与受管发行资产，并以安装/命令测试验证固定 `deploy`、`status`、`health`、无输入 `rollback` 能运行且不接受任意命令。
- [x] 2.2 提供 staging 专用 Runner、Compose、systemd、状态和 ACR pull-only 配置模板，使用 `/opt/gap-staging-runner` 与 `/opt/gap-staging`；以资产测试验证它拒绝 production 路径、凭据、证书和状态引用。
- [x] 2.3 使用宿主机已安装的 Caddy 提供 staging Caddyfile、安装/校验资产和 Compose 私有网络，固定 `staging.gapclaw.online`、`runner-staging.gapclaw.online` 和 `gap-runner-staging.internal:9443`；以 Caddy/Compose 测试验证公网 callback 不代理 `/v1/*`、私有控制面不公开、不引用 Nginx 且不能路由 production 服务。

## 3. 隔离契约与演练证据

- [x] 3.1 增加 staging 跨层契约测试，覆盖 digest 部署、健康成功、失败回滚、GAP 暂不可用回传重试、重启后对账和管理员确认 rollback，同时断言 staging 记录不混入 production。
- [x] 3.2 更新 staging 部署/运维文档，说明宿主机 Caddy、GitHub Environment、远程主机初始化、staging ACR pull-only 身份、DNS/TLS/mTLS、首个健康基线、演练和撤销流程；以文档路径/命令契约测试验证与资产一致。
- [ ] 3.3 在用户授权的远程 staging 主机完成一次受控发布演练，记录脱敏 manifest、Runner status/health、回传重试、确认 rollback 和 production 隔离证据；验证失败时不执行 production 操作。
