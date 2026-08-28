# 生产发布（Git Tag → ACR → Self-hosted Runner）

推送形如 `v1.0.0` 的 Git tag 后，GitHub Actions 在 GitHub-hosted runner 上用 Buildx 构建 **linux/amd64 + linux/arm64** 镜像，推送到阿里云 ACR，再由目标机上的 Self-hosted Runner 只做 `docker compose pull/up` 和健康检查。服务器**不编译、不 git pull 业务代码**。

本地开发仍使用 `deploy/docker-compose.yml` 与 `deploy/build-local.sh`（按架构打 `gap-api-amd` / `gap-api-arm` 单架构标签）。生产多架构镜像不再带 `-amd`/`-arm` 后缀。

## GitHub Secrets

在仓库 **Settings → Secrets and variables → Actions** 配置：

| Secret | 含义 | 示例 |
| --- | --- | --- |
| `ALIYUN_REGISTRY` | ACR 域名 | `crpi-xxxx.cn-chengdu.personal.cr.aliyuncs.com` |
| `ALIYUN_REGISTRY_USERNAME` | ACR 用户名 | 阿里云 RAM / ACR 登录名 |
| `ALIYUN_REGISTRY_PASSWORD` | ACR 密码 | 只放 Secret，禁止写入 Git |
| `ALIYUN_IMAGE` | **API** 仓库路径（不含 tag） | `tools_claw/gap-api` |

最终镜像：

```text
${ALIYUN_REGISTRY}/${ALIYUN_IMAGE}:v1.0.0
${ALIYUN_REGISTRY}/${ALIYUN_IMAGE}:latest
```

Web 镜像由 API 路径推导：若 `ALIYUN_IMAGE` 以 `-api` 结尾，则换成 `-web`（`tools_claw/gap-api` → `tools_claw/gap-web`），否则追加 `-web`。

数据库镜像**不随 tag 构建**。在服务器 `/opt/gap/.env` 的 `IMAGE_DB`（或沿用 `GAP_IMAGE_DB`）填写已有 Postgres/pgvector 地址。

## 服务器首次初始化

以下在**目标生产机**执行。Runner labels 必须包含 `self-hosted`、`linux`、`production`。

### 1. 安装 Docker

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo systemctl enable --now docker
```

### 2. 安装 Docker Compose Plugin

```bash
docker compose version
```

若未安装，按 [Docker Compose 插件说明](https://docs.docker.com/compose/install/linux/) 安装 `docker-compose-plugin`。

### 3. 创建部署目录

```bash
sudo mkdir -p /opt/gap/data
sudo chown -R "$USER:$USER" /opt/gap
```

将仓库中的 `deploy/.env.example` 复制为服务器私密配置（**不要**提交真实 `.env`）：

```bash
cp deploy/.env.example /opt/gap/.env
chmod 600 /opt/gap/.env
```

至少填写：

- `IMAGE_API` / `IMAGE_WEB`（可与 Secrets 中的 ACR 路径一致；Actions 部署时会覆盖 tag）
- `IMAGE_DB`
- `GAP_DATA_DIR=/opt/gap/data`
- `DOCKER_DATA_HOST_PATH=/opt/gap/data`
- `CORS_ORIGINS`（生产域名）
- `SECRET_KEY`、`ADMIN_PASSWORD`（不要用示例占位）
- `ALIYUN_REGISTRY` / `ALIYUN_REGISTRY_USERNAME` / `ALIYUN_REGISTRY_PASSWORD`（若仅靠 Actions 注入也可留空，由 workflow env 提供）

### 4–6. 安装并注册 GitHub Actions Self-hosted Runner（systemd）

在仓库 **Settings → Actions → Runners → New self-hosted runner** 按 Linux 提示下载。示例（版本号以 GitHub 页面为准）：

```bash
mkdir -p "$HOME/actions-runner" && cd "$HOME/actions-runner"
curl -o actions-runner-linux.tar.gz -L https://github.com/actions/runner/releases/latest/download/actions-runner-linux-x64-2.321.0.tar.gz
tar xzf actions-runner-linux.tar.gz
./config.sh --url https://github.com/<ORG_OR_USER>/gapclaw --token <REGISTRATION_TOKEN> \
  --labels self-hosted,linux,production --name gap-prod-1
sudo ./svc.sh install
sudo ./svc.sh start
```

ARM 机器请下载 `actions-runner-linux-arm64-*.tar.gz`。`--token` 一次性注册令牌来自 GitHub 页面，不要写入仓库。

### 7. Docker 权限

Runner 进程用户必须能无 sudo 调用 Docker：

```bash
sudo usermod -aG docker "$USER"
```

安装成 systemd 服务后，重启 runner 服务使组生效：

```bash
sudo ./svc.sh stop && sudo ./svc.sh start
docker ps
```

### 8. 部署文件

生产 compose 在仓库 `deploy/docker-compose.prod.yml`。workflow 会 checkout **该 tag** 再执行 `./scripts/deploy.sh`，因此不需要在服务器上 `git pull` 业务。`/opt/gap/.env` 与数据目录必须留在仓库 checkout 之外，避免被工作区清理删掉。

可选：`export DEPLOY_ENV_FILE=/opt/gap/.env` 写入 runner 的 `.env`（`actions-runner/.env`）或 systemd 环境。脚本默认即读取 `/opt/gap/.env`。

### 9. GitHub Secrets

见上文表格。不要把 ACR 密码写入仓库或 `docs/`。

### 10. 第一次发布验证

```bash
git tag v1.0.0
git push origin v1.0.0
```

然后确认：

1. Actions 中 **Release** workflow 的 `build` 成功（双架构 push）。
2. `deploy` job 跑在带 `production` 标签的 self-hosted runner。
3. `curl -sf http://127.0.0.1:8000/health` 返回 `"status":"ok"`。
4. 浏览器打开 Web 端口（默认 5173）。

## 发布命令

```bash
git tag v1.0.0
git push origin v1.0.0
```

并发：workflow `concurrency.group: production-deploy` 且 `cancel-in-progress: false`，同一时刻只跑一条生产发布。

## 回滚

服务器已有旧 tag 镜像时，直接再部署该 tag（镜像仍在 ACR）：

```bash
./scripts/deploy.sh v0.9.0
```

或重新推送/重跑旧 tag 的 workflow。回滚**不会**自动改 Git 默认分支，只切换 Compose 使用的 `IMAGE_TAG`。

## 验证多架构镜像

```bash
# 清单应同时包含 amd64 与 arm64
docker buildx imagetools inspect ${ALIYUN_REGISTRY}/${ALIYUN_IMAGE}:v1.0.0
docker buildx imagetools inspect ${ALIYUN_REGISTRY}/tools_claw/gap-web:v1.0.0
```

目标机：

```bash
uname -m                    # x86_64 → amd64；aarch64 → arm64
docker compose -p gap -f deploy/docker-compose.prod.yml ps
curl -sf http://127.0.0.1:8000/health
```
