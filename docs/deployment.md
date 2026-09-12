# GAP 生产发布与运维

本流程使用不可变镜像 digest、固定主机 Deploy Runner 和受限 Release Agent 完成 GAP 自身发布。生产主机不检出 Git tag，也不执行 tag 中的脚本或 Compose 文件。

## 发布链路与权限边界

1. 推送 `vMAJOR.MINOR.PATCH` tag 后，`Release` 工作流要求它与该提交的 `deploy/gap.version` 完全一致。
2. GitHub-hosted 构建任务向 ACR 推送 API/Web 镜像，并上传含 API/Web digest 的 `gap-release-manifest`。
3. 同一个 GitHub-hosted job 生成唯一 delivery id 与 UTC 时间戳，使用 GitHub secret `GAP_RELEASE_HOOK_SECRET` 对规范 JSON envelope 作 HMAC-SHA256 签名，并且只向固定地址 `POST https://gapclaw.online/internal/release-hook` 投递。
4. GAP 的非 LLM Hook receiver 在验签、五分钟时窗、delivery/release 去重、目标、tag/version、SHA 与 digest 格式均通过后，才以其固定 mTLS 身份调用私有 `https://gap-runner.internal:9443/v1/deploy`。Runner 再次校验 manifest、允许仓库与目标，才使用 `/opt/gap-runner/compose/gap-prod.compose.yml` 操作 Compose。

GitHub 不 checkout 生产主机、不读取 `scripts/deploy.sh`、不执行仓库 Compose 文件，也不接收 ACR pull 凭据。生产服务器不安装 GitHub Actions Runner、不需要 runner token，也不需要访问 GitHub；它只需访问 ACR 拉取已固定的镜像 digest。

### GitHub Hook secret

在仓库 Actions secrets 中创建至少 32 字符的随机 `GAP_RELEASE_HOOK_SECRET`，并将**完全相同的值**仅写入生产主机 `/opt/gap/.env` 的 `RELEASE_HOOK_SECRET`。不要把它设为 ACR 密码、GitHub PAT、证书私钥或可猜测版本号。GitHub 工作流不打印该值，GAP 不将它写入发布审计、API 响应或日志。

## 首次主机安装

在生产主机安装 Docker Engine、`docker compose` 插件和已由主机运维的 Caddy。GAP Compose 不创建也不加入公共代理网络；API 与 Web 仅发布到 host loopback。安装随已审核 Runner 发布包提供的固定资产：

```bash
sudo sh tools/gap_deploy_runner/assets/initialize-host.sh
sudo sh tools/gap_deploy_runner/assets/install-production-caddy.sh
sudo systemctl daemon-reload
```

初始化脚本创建 `/opt/gap-runner`、状态目录、固定 Compose 资产、`Caddyfile.production` 副本和 `gap-deploy-runner.service`。`install-production-caddy.sh` 仅在主 Caddyfile 已显式导入 `/etc/caddy/sites/*.Caddyfile`、且 production callback CA 已就位时，安装 `/etc/caddy/sites/gap-production.Caddyfile`，先执行 `caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile`，再 reload Caddy；它不会覆盖主 Caddyfile，也不会启动或加载 Nginx。将已审核的 Runner 二进制分别安装到：

```text
/opt/gap-runner/bin/gap-deploy-runner
/opt/gap-runner/bin/gap-deploy-runner-server
```

服务以 `gap-runner` 用户运行；不要以 root、GitHub Runner 用户或 GAP 应用容器身份直接执行部署。

### 主机配置与 ACR pull 凭据

`/opt/gap/.env` 是唯一的主机受管应用和 ACR pull 配置。它至少包含：

```dotenv
ALIYUN_REGISTRY=registry.example.com
ALIYUN_REGISTRY_USERNAME=host-pull-user
ALIYUN_REGISTRY_PASSWORD=host-pull-password
ALIYUN_IMAGE=namespace/gap-api
IMAGE_DB=registry.example.com/namespace/gap-db@sha256:...
GAP_DATA_DIR=/opt/gap/data

RELEASE_ENVIRONMENT=production
RELEASE_RUNNER_CA_FILE=/run/gap-release-mtls/ca.crt
RELEASE_RUNNER_CLIENT_CERT_FILE=/run/gap-release-mtls/gap-client.crt
RELEASE_RUNNER_CLIENT_KEY_FILE=/run/gap-release-mtls/gap-client.key
RELEASE_RUNNER_TIMEOUT_SECONDS=10
RELEASE_HOOK_SECRET=replace-with-the-same-32-plus-character-github-secret
RELEASE_HOOK_MAX_AGE_SECONDS=300
```

该文件只供主机 Runner/Compose 使用，不得加入 Git、GitHub Secrets 的部署 job、浏览器响应或发布审计。文件须由 `gap-runner` 独占可读：

```bash
sudo chown gap-runner:gap-runner /opt/gap/.env
sudo chmod 0600 /opt/gap/.env
```

Runner 的监听/服务端 TLS 配置在 `/opt/gap-runner/runner.env`；可从 `runner.env.example` 创建后填写。它的 `GAP_RUNNER_LISTEN` 应绑定 Docker bridge（示例为 `172.17.0.1:9443`），主机防火墙只允许该 bridge 访问 9443，不得向公网开放。

### 私有控制面与公网回调

- GAP API 容器通过 Compose 中唯一的 `gap-runner.internal:host-gateway` 映射访问 `https://gap-runner.internal:9443`。该地址仅服务由 GAP `gap-client` mTLS 身份调用的 `POST /v1/deploy`、`GET /v1/status`、`GET /v1/health` 和无输入的 `POST /v1/rollback`；deploy 只接受完整的已验证 manifest。
- `/opt/gap/release-mtls` 只读挂载到 API 容器的 `/run/gap-release-mtls`，保存 GAP 客户端所需 CA、证书和私钥。不要把这些材料放进镜像或 API 响应。
- `https://runner.gapclaw.online/internal/release-runner/callback` 是另一个方向：它仅接受 Runner 到 GAP 的 `POST`，由宿主机 Caddy 以 `require_and_verify` mTLS 验证。它不代理 `/v1/*`；任何其他 callback host 路径返回 404，未持受信任客户端证书的请求在到达 GAP 前被拒绝。
- `gapclaw.online` 仅由宿主机 Caddy 提供 GAP Web/API 和精确的 `POST /internal/release-hook`。Hook 仅反代到 `127.0.0.1:8000`，同一路径的其他方法为 404；明确列出的 API/WebSocket 路径也反代到 API，其余浏览器流量代理至 `127.0.0.1:8080`。GAP API/Web 不直接绑定公网接口，也不加入可被后续 Compose 栈共享的代理网络。

### Caddy 站点与后续 Compose 扩展

主机 Caddy 主配置必须保留这一已审核 import（可与主机其他安全全局配置并存）：

```caddyfile
import /etc/caddy/sites/*.Caddyfile
```

production site 使用 `gapclaw.online` 和固定 callback host `runner.gapclaw.online`。`gapclaw.online` 可使用 Caddy 自动证书；Runner callback host 是机器到机器入口，使用 release 私有 CA 签发的固定服务端证书，避免把 callback 可用性绑定到公网 ACME challenge。callback 证书和信任 CA 位于：

```text
/etc/caddy/production/runner-server.crt
/etc/caddy/production/runner-server.key
/etc/caddy/production/runner-ca.crt
```

这些材料只授予 Caddy 读取权限，且必须与 staging CA 分离。后续第二个 Compose 栈必须使用独立的显式域名或子域名、独立 loopback upstream 与单独的 `/etc/caddy/sites/*.Caddyfile` site；不得复用 GAP site、取得 catch-all 路由、绑定 80/443，或访问 GAP API 容器网络。

配置完成后启用 Runner 服务：

```bash
sudo systemctl enable --now gap-deploy-runner
sudo systemctl status gap-deploy-runner
```

## mTLS 轮换

使用私有 CA 为 Runner 服务端证书签发 `gap-runner.internal` SAN，并为 GAP API 与 Runner callback 分别签发客户端身份。维护这些受管位置：

```text
/opt/gap-runner/tls/ca.crt
/opt/gap-runner/tls/runner.crt
/opt/gap-runner/tls/runner.key
/opt/gap/release-mtls/ca.crt
/opt/gap/release-mtls/gap-client.crt
/opt/gap/release-mtls/gap-client.key
/etc/caddy/production/runner-server.crt
/etc/caddy/production/runner-server.key
/etc/caddy/production/runner-ca.crt
```

轮换时先让新旧 CA 在信任链中重叠，替换客户端/服务端证书后重启 Runner 与受影响的 GAP Compose 服务，再撤销旧 CA。每次轮换均验证私有 Runner 的 status/health、受保护 callback 以及未持证书客户端被拒绝。私钥必须不可被其他用户读取，且不得记录到日志或审计表。

## 发布、健康基线与回滚

首次发布没有已知健康版本；若健康检查失败，Runner 记录 `reconciliation_required`，不会猜测镜像。先完成一次成功的 digest 发布，确认 Compose 服务健康且 API `/health` 返回 `{"status":"ok"}`，再将其作为首个 `last_known_healthy` 基线。

日常发布只需：

```bash
git tag v1.2.3
git push origin v1.2.3
```

随后在 GitHub Actions 中确认 `Release` 的 Hook 步骤收到 202；在 Release Management 中确认同一 delivery 的 `received` 审计及后续 Runner 回传。若 GAP 到 Runner 的请求中断，保留 `dispatch_failed`/`reconciliation_required` 记录并执行对账；不要重放 delivery、手工改 Hook URL 或传递新镜像。

通过「发布管理」查看状态、健康、自动回滚和历史。只有管理员可见受确认回滚；必须选择 Runner 显示的已知健康目标并输入 `ROLLBACK`。界面不能接受 tag、镜像或 digest。

紧急情况下，经过授权的主机操作员可绕过 GAP UI，调用固定无输入回滚操作：

```bash
sudo -u gap-runner /opt/gap-runner/bin/gap-deploy-runner rollback
```

随后检查 Runner status/health 与 GAP 发布管理历史是否一致；不一致时保持 `reconciliation_required`，不要重新执行或手工指定镜像。

## Code Agent 边界

既有 Code Agent Docker socket 挂载未在本变更中迁移或移除，但它不属于 Release Agent 的权限。Release Agent 没有 Docker、shell、SSH、MCP、通用 URL/命令执行或 ACR 写入凭据；所有 GAP 自部署只能由固定 Deploy Runner 完成。
