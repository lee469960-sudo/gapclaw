# GAP — AI Agent Platform

Independent reimplementation of the GAP intelligent workbench.

## Stack

- **API**: Python 3.11, FastAPI, SQLAlchemy, WebSockets
- **Web**: Vue 3, Element Plus, Vite
- **Runtime**: Docker (sandboxes), PostgreSQL/SQLite

## Quick start

### 本地一键启动（推荐测试）

```bash
chmod +x start.sh scripts/*.sh
./start.sh
```

会自动：安装依赖、启动 API（:8000）+ Vite 前端（:5173）、打开登录页。

- 地址：http://127.0.0.1:5173/login
- 账号：`admin` / `admin123`
- 停止：`./scripts/stop-local.sh`
- 日志：`.local/logs/`
- 系统日志分析 Agent：见 [docs/system-log-analyst.md](docs/system-log-analyst.md)（Agent `log-analyst`）

### 手动启动

```bash
# Backend（本地开发最终从 app 目录启动，避免 --reload 监视 data/ 下的 Workspace）
cd apps/api
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
cd app
uvicorn app.main:app --app-dir .. --env-file ../.env --reload --reload-dir . --port 8000

# Frontend
cd apps/web
npm install
npm run dev
```

Or with Docker Compose:

```bash
cd deploy
docker compose up --build
```

Default admin: `admin` / `admin123`

> **升级说明**：默认 Postgres 用户/库已从 `gclaw` 改为 `gap`。已有 `pgdata` 卷仍是旧凭据时，需 `docker compose down -v` 后重建库，或手工改库用户；本地 SQLite 默认路径为 `./data/gap.db`。

## MiniMax LLM + DBA Agent

在 `apps/api/.env` 中配置 MiniMax（OpenAI 兼容接口）：

```env
MINIMAX_API_KEY=sk-cp-...
MINIMAX_BASE_URL=https://api.minimax.io/v1
MINIMAX_MODEL=MiniMax-M3
SEED_DBA_AGENT=true
```

国内用户可将 `MINIMAX_BASE_URL` 改为 `https://api.minimaxi.com/v1`。

API 启动时会幂等 seed：

- **MinMax** LLM（需 `MINIMAX_API_KEY` 非空）
- **dba-sandbox** 沙箱
- **dba** Agent（需 `SEED_DBA_AGENT=true`）

一键重启并验证：

```bash
chmod +x scripts/seed-dba.sh
./scripts/seed-dba.sh
```

验收步骤：

1. 打开 http://127.0.0.1:5173/llms — 可见 **MinMax** 卡片，点「测试」应返回正常回复
2. 打开 http://127.0.0.1:5173/agents — 可见 **dba** 卡片（沙箱/LLM 标签正确）
3. 点「+ 创建 Agent」— 双栏弹窗，LLM/沙箱为下拉选择
4. 进入 dba 对话页，发送消息可触发 ReAct（沙箱 running 时效果最佳）

## 沙箱与镜像

沙箱管理页（http://127.0.0.1:5173/sandboxes）支持卡片列表、镜像管理弹窗，对接本地 Docker。

在 `apps/api/.env` 中确认：

```env
DOCKER_SOCKET=unix:///var/run/docker.sock
DEFAULT_SANDBOX_IMAGE=myclaw-base:latest
```

验收步骤：

1. 确认 Docker Desktop 运行，本地存在 `myclaw-base:latest`（`docker images | grep myclaw-base`）
2. 打开 http://127.0.0.1:5173/sandboxes — 卡片列表，支持所有/我的 Tab
3. 点「镜像管理」— 列表应含 `myclaw-base:latest`（含 ID、大小、创建时间）
4. 创建沙箱，镜像选 `myclaw-base:latest`，保存后「更多 → 启动」
5. 状态变为 running 后，点「执行命令」进入终端
6. 镜像管理可测试拉取、下载（export tar）、上传（import tar）、删除

### Docker Hub 超时（国内网络）

**原因**：`docker compose up --build` 默认走 buildx 容器驱动，会去 `docker.io` 拉元数据，容易 `auth.docker.io ... i/o timeout`。

**推荐**：用本地已有镜像 + 宿主机 Docker 构建：

```bash
cd deploy
chmod +x build-local.sh
./build-local.sh
```

脚本会：
1. 用 `--pull=false` 读取本地 `python:3.12-slim`、`node:20-alpine`
2. 按当前 CPU 架构构建并打 tag（`gap-api-{arm|amd}:V0.0.1`、`gap-web-{arm|amd}:V0.0.1`）
3. `docker compose up -d --no-build` 启动

版本号在 `deploy/gap.version`（当前 `V1.0.9`）。构建双架构：

```bash
cd deploy
chmod +x build-images.sh build-local.sh
./build-images.sh all    # arm + amd
./build-images.sh arm    # 仅 arm64
./build-images.sh amd    # 仅 amd64
```

手动分步：

```bash
# 1. 确认本地有基础镜像（你已有可跳过）
docker images | grep -E 'python.*3.12|node.*20-alpine'

# 2. 宿主机直接 build（不要用 buildx 多架构 builder）
export GAP_ARCH=arm   # 或 amd
export GAP_VERSION=V0.0.1
docker build --pull=false --platform linux/arm64 \
  -t gap-api-${GAP_ARCH}:${GAP_VERSION} -f apps/api/Dockerfile apps/api
docker build --pull=false --platform linux/arm64 \
  --build-arg VITE_API_BASE=http://localhost:8000 \
  -t gap-web-${GAP_ARCH}:${GAP_VERSION} -f apps/web/Dockerfile apps/web

# 3. 启动（不再 build）
cd deploy && GAP_ARCH=arm docker compose up -d --no-build
```

### 远程服务器部署（阿里云镜像）

旧版 `docker-compose` 不支持 `${VAR:-default}`，请使用 `deploy/.env`：

```bash
cd deploy
cp .env.example .env
# 编辑 .env：GAP_ARCH=amd（或 arm），确认 GAP_REGISTRY / GAP_NAMESPACE
chmod +x render-env.sh
./render-env.sh

# 登录阿里云镜像仓库后启动
docker login crpi-scibloubqf62j8nh.cn-chengdu.personal.cr.aliyuncs.com
docker compose up -d
# 若只有 docker-compose v1：
docker-compose up -d
```

`.env` 示例（x86 服务器）：

```env
GAP_ARCH=amd
GAP_VERSION=V0.0.1
GAP_REGISTRY=crpi-scibloubqf62j8nh.cn-chengdu.personal.cr.aliyuncs.com
GAP_NAMESPACE=tools_claw
CORS_ORIGINS=http://你的服务器IP:5173
```

执行 `./render-env.sh` 后会自动生成：

```env
GAP_IMAGE_API=crpi-.../tools_claw/gap-api-amd:V0.0.1
GAP_IMAGE_WEB=crpi-.../tools_claw/gap-web-amd:V0.0.1
GAP_IMAGE_DB=crpi-.../tools_dba/postgres-amd:16-alpine
```

若仍想用 `docker compose build`，先切回默认 builder：

```bash
docker buildx use default
docker context use default
```

## Modules

| Module | Path |
|--------|------|
| Agent groups | `/groups` |
| Agents + chat | `/agents` |
| Sandboxes | `/sandboxes` |
| Skills market | `/skills` |
| MCP market | `/mcps` |
| LLM market | `/llms` |
| IM channels | `/channels` |
| SQL tool | `/sql` |
| SSH terminal | `/terminals` |
| File manager | `/files` |
| RAG | `/rag` |
| System | `/system` |

IM Bot 开通步骤见 [docs/im-channels.md](docs/im-channels.md)。
