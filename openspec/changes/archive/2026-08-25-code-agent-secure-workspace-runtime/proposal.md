## Why

CodeAgent V1 已具备 Workspace、Code Tools 和控制面骨架，但仓库仍可能由 API 进程直接获取，部分代码工具也仍在 API 容器内执行；这会绕过统一沙箱边界，并使私有仓库凭据、Compose bind path、源码完整性和失败清理难以可靠审计。现在需要先建立安全的源码导入与全容器化运行基线，后续才能在其上加载项目 Skill 并开展真实仓库验证。

## What Changes

- 增加受控 Repository Source：支持 allowlist 内的 HTTPS、SSH 和经管理员显式允许的内部 HTTP Git 源，以及管理员配置只读根目录下的本地仓库快照导入。
- 私有仓库仅通过 Secret Store 中的只读 Deploy Token 引用认证；凭据不得写入 URL、Manifest、日志、模型上下文、runner 容器或 Workspace。
- 发布 Manifest 时解析 branch/ref 并冻结精确 commit SHA；run 仅消费该不可变源码快照，不直接访问 Git 服务，也不跟随分支漂移。
- Repository Importer 实施主机名与端口精确 allowlist、DNS/IP 与重定向校验、路径与符号链接逃逸防护、协议限制、大小/文件数/超时限制，并在 V1 拒绝 submodule 和 Git LFS。
- **BREAKING**：移除 CodeAgent 混合执行方式，`read/search/edit/git/shell/test` 全部只在每个 run 的专用 runner 容器内执行；API 进程仅负责鉴权、调度、策略、审计和工件协调。
- 增加 Docker Compose 的 API 容器路径到 Docker daemon 主机 bind path 的显式映射与启动前校验，并用真实 Docker 集成测试覆盖挂载正确性。
- 强化 runner：默认断网、只读 rootfs、drop all capabilities、`no-new-privileges`、资源限制，且仅 Workspace 可写；容器在所有终态立即移除。
- 对导入源码和最终 patch 执行 fail-closed 秘密扫描；扫描被截断、不支持或执行失败均不得进入执行或 `patch_ready`。
- Workspace 默认保留 7 天用于审计和故障分析，之后按策略清理；工件与审计记录使用独立的更长期保留策略。任何保留的 Workspace 均不得继续执行，且不得获得原仓库写权限。
- 明确本 change 不包含 Skill 解析/冻结、`dbt-clickhouse-gamestat` 上下文注入、用户操作文档、黄金任务或端到端效果验收；这些由后续独立 changes 处理。

## Capabilities

### New Capabilities

- `code-agent-repository-source`: 定义远程 Git 与本地只读根目录的受控导入、Secret 引用、ref 冻结、SSRF/路径防护、来源限制及不可变源码快照契约。
- `code-agent-sandbox-runtime`: 定义所有 Code Tools 的专用容器执行边界、Compose bind path 映射、容器加固、网络/资源策略与容器生命周期。

### Modified Capabilities

- `code-agent-control-plane`: 扩展 Project/Manifest 的仓库来源、凭据引用、已解析 commit 与 readiness 校验及管理权限契约。
- `code-agent-project-policy`: 将来源 allowlist、运行时权限交集、资源预算收紧、平台停止开关和失败原因纳入不可绕过的有效策略。
- `code-agent-workspace`: Workspace 改为从受控不可变快照准备，禁止 API 主机工具访问，并将终态清理细化为容器立即删除与 Workspace 限时保留/到期清理。
- `code-agent-verification`: 在执行前源码与封存前 patch 两个阶段实施完整、fail-closed 的秘密扫描和安全门禁。

## Impact

- 影响 Code Project/Manifest 数据模型与 API、Secret Store 引用、仓库导入服务、WorkspaceManager、Code Tool adapters、Docker runner、Verifier、审计事件和后台清理任务。
- 需要部署侧明确配置 Git 来源 allowlist、本地只读仓库根目录、可信 runner 镜像及 API 容器路径到 Docker 主机路径映射。
- 需要数据库迁移并兼容读取既有项目；既有 Manifest 在补齐安全来源并重新发布前不得被视为新 run 可用。
- 需要单元、契约、集成与真实 Docker 测试，覆盖私有仓库认证但不泄密、ref 漂移、SSRF/重定向、符号链接逃逸、Compose 挂载、全工具容器隔离、扫描失败、资源限制以及终态清理。
- 不改变 ReAct-Engine 统一运行时或 Standard Agent 默认行为，也不授予自动 commit、push、PR 或原仓库写入能力。
