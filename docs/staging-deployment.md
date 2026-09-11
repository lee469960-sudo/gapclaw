# GAP Staging 发布与演练

Staging 是 production 隔离的完整发布演练环境。它只部署不可变 API/Web digest，绝不读取 production 的 Runner、状态、网络、证书、域名或凭据。本手册中的值是固定契约，不允许由浏览器、API 请求、manifest 或 Agent 修改。

## 1. GitHub 与镜像准备

1. 在仓库创建受保护的 `staging` GitHub Environment；只允许默认分支中的 `Deploy GAP Staging` workflow 和授权操作员通过环境审批。
2. 将远程主机的 GitHub Actions Runner 注册为专用标签：`self-hosted`、`linux`、`staging`。不要在这台 Runner 上添加 `production` 标签。
3. 为 staging 创建独立的 ACR 身份 `gap-staging-pull`，只授予 `AcrPull`。它不能拥有 push、delete、管理或 production 仓库权限；其秘密仅放入 `/opt/gap-staging/.env`，绝不放入 GitHub workflow。
4. `Release` 成功后会产生 `gap-staging-release-manifest`。`Deploy GAP Staging` 只下载该产物并执行固定命令：

   ```bash
   /opt/gap-staging-runner/bin/gap-deploy-runner deploy --manifest "${RUNNER_TEMP}/gap-staging-release-manifest/release-manifest-staging.json"
   ```

   workflow 不 checkout tag、不执行仓库部署脚本，也不登录 ACR。

## 2. 远程 Staging 主机初始化

使用一台不运行 production Runner/Compose 的授权 Linux 主机，确认已安装并启用 Caddy，随后安装 Docker Engine 和 `docker compose` 插件。以经过审核的 Runner 发行包初始化：

```bash
sudo sh tools/gap_deploy_runner/assets/initialize-staging-host.sh
sudo systemctl daemon-reload
```

初始化后只应使用以下根目录：

```text
/opt/gap-staging-runner             # Runner、二进制、状态与 Runner TLS
/opt/gap-staging                    # GAP .env、数据与 API mTLS 挂载
/opt/gap-staging-runner/state/release-state.json
```

从受管模板创建并收紧权限：

```bash
sudo install -o gap-staging-runner -g gap-staging-runner -m 0600 \
  /opt/gap-staging/.env.example /opt/gap-staging/.env
sudo install -o root -g gap-staging-runner -m 0640 \
  /opt/gap-staging-runner/runner.env.example /opt/gap-staging-runner/runner.env
```

`runner.env` 必须保留 `GAP_RUNNER_TARGET_ID=staging`，并绑定 Docker bridge 上的 `GAP_RUNNER_LISTEN`。`/opt/gap-staging/.env` 必须设为 `RELEASE_ENVIRONMENT=staging`，含 staging 专用 ACR pull-only 身份和不可变 DB digest。不得复制 `/opt/gap-runner`、`/opt/gap` 或 production 的 `.env`、CA、证书、私钥与 state 文件。API 与 Web 只发布到 `127.0.0.1:18000` 和 `127.0.0.1:18080`，由宿主机 Caddy 访问，绝不直接暴露公网端口。

## 3. DNS、TLS 与 mTLS

准备只属于 staging 的 DNS 和证书：

- 浏览器入口：`staging.gapclaw.online`。
- Runner 回调入口：`runner-staging.gapclaw.online`，Caddy 只允许带 staging Runner 客户端证书的 `POST /internal/release-runner/callback`。
- 私有控制面：`https://gap-runner-staging.internal:9443`，只通过 GAP API Compose 的 `host-gateway` 访问；不能配置公网 DNS 或 Caddy 路由。

将 staging Runner 的服务端 CA/证书/私钥放入 `/opt/gap-staging-runner/tls/`；将 GAP 到 Runner 的客户端材料放入 `/opt/gap-staging/release-mtls/`；将 callback 信任 CA 放入 `/etc/caddy/staging/runner-ca.crt`。这三组材料必须由 staging 专用 CA/identity 签发，不能复用 production PKI。

Caddy 主配置 `/etc/caddy/Caddyfile` 须经审核后包含唯一导入：

```caddyfile
import /etc/caddy/sites/*.Caddyfile
```

在 CA 文件就位后安装并校验 staging site；该脚本会先执行 `caddy validate`，只有校验成功才 reload 已安装的 Caddy 服务：

```bash
sudo sh tools/gap_deploy_runner/assets/install-staging-caddy.sh
```

它安装 `Caddyfile.staging`，并确认 Caddy 对 callback host 的其他路径返回 404。公网地址不得代理 `/v1/status`、`/v1/health` 或 `/v1/rollback`。不得启动 Nginx 容器或加载 Nginx 配置。

## 4. 启动与首次健康基线

完成 mTLS 与 `.env` 后，启用 Runner：

```bash
sudo systemctl enable --now gap-deploy-runner-staging
sudo systemctl status gap-deploy-runner-staging
sudo -u gap-staging-runner /opt/gap-staging-runner/bin/gap-deploy-runner status
sudo -u gap-staging-runner /opt/gap-staging-runner/bin/gap-deploy-runner health
```

首次发布前不存在已知健康版本。先从受保护 workflow 完成一次 staging digest 发布，确认 Runner `status` 显示 `succeeded`、`health` 为 `ok`，并在 Release Management 中看到同一 staging 记录；此记录才成为回滚基线。健康失败会自动回到该基线；没有基线时保持 `reconciliation_required`，不要手动指定镜像。

## 5. 受控演练、回滚与撤销

每次演练保存不含秘密的证据：脱敏 manifest 的 release ID/target/digest、Runner `status`/`health`、callback 暂不可用后的重试结果、Release Management 审计以及管理员回滚请求。证据必须标记 `staging`。

管理员先在 Release Management 读取 Runner 给出的已知健康版本，确认目标为 `staging`，输入 `ROLLBACK` 后再提交。紧急情况下仅允许主机操作员执行无输入命令：

```bash
sudo -u gap-staging-runner /opt/gap-staging-runner/bin/gap-deploy-runner rollback
```

撤销 staging 时，先禁用 GitHub `staging` Environment 的部署审批和 Runner，再停止 `gap-deploy-runner-staging`，撤销 staging ACR identity 与 staging 证书/DNS。保留 staging state 和脱敏日志以便对账；不要在撤销或故障处理中执行 production 命令。
