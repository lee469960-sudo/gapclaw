# code-agent-sandbox-runtime Specification

## Purpose

定义 CodeAgent Code Tools 的单一容器执行边界、部署路径映射、运行时加固和资源生命周期，使代码操作不能退回 API 进程或越过 Workspace。

## Requirements

### Requirement: Claude Code runner image 必须固定版本并通过 preflight

支持 Claude Code runtime 的 runner image SHALL 预装 Claude Code CLI，并由平台批准的 image digest 固定。每次 run 启动前 SHALL 验证 CLI 版本、配置目录、repository cwd、MCP 配置、模型连接和预算 watchdog；失败时 fail closed，不得动态安装或回退宿主执行。

#### Scenario: 可信镜像包含 Claude Code

- **WHEN** Manifest 选择支持 Claude Code runtime 的可信 runner image digest
- **THEN** runner 中存在固定版本的 Claude Code CLI
- **AND** preflight 记录版本和 digest 事实

#### Scenario: Claude Code CLI 不可用

- **WHEN** runner image 中 Claude Code CLI 缺失、版本不可识别或无法执行
- **THEN** 系统以 `runtime_unavailable` 终止准备
- **AND** 不通过网络动态安装 Claude Code，也不从宿主机 bind mount CLI

### Requirement: Claude Code 临时配置必须位于 run-local runtime 目录

系统 SHALL 将 Claude Code 的 skills、MCP config、settings、transcript 和其他 runtime 临时文件写入当前 run Workspace 下的受控 runtime 目录或等效 run-local 目录。该目录 MUST NOT 污染业务 repository diff，MUST NOT 写入宿主机 `~/.claude`，并 SHALL 随 Workspace 生命周期清理或按审计策略保留。

#### Scenario: 生成 run-local 配置

- **WHEN** runtime adapter 准备 Claude Code 配置
- **THEN** 系统在 run-local 目录生成 `.claude/skills`、MCP config 和 runtime settings
- **AND** 这些配置不被纳入业务 patch 的 canonical diff

#### Scenario: Runtime 配置包含秘密引用

- **WHEN** MCP 或模型配置需要凭证
- **THEN** 系统通过 sandbox secret/env 注入引用或值
- **AND** run-local 配置文件不得保存明文秘密

### Requirement: runner 必须使用不可绕过的隔离配置

CodeAgent SHALL 在 Agent 已绑定的持久 Sandbox 内执行 Claude Code，而不是为此创建一次性独立 runner。`CodeContainerRunner` SHALL 拒绝 privileged、额外挂载和 Docker socket。Docker socket、Secret Store、API 文件系统、其他 run Workspace 和未批准宿主路径 MUST NOT 被挂载进该 Sandbox。

当冻结契约为 `coding_runtime=claude_code` 时，平台授权的网络例外 SHALL 为 Docker `bridge`。该例外 MUST NOT 被描述为域名 allowlist。`coding_runtime=legacy` 或未选择 Claude Code 时，适配器记录的网络模式 MUST 为 `none`。`host` 及其他非 `none`/`bridge` 模式 MUST 被拒绝。

#### Scenario: 默认启动隔离 runner

- **WHEN** 有效 Code run 绑定已运行的持久 Sandbox
- **THEN** 适配器复用该 Sandbox，不启动 privileged 容器，也不挂载 Docker socket
- **AND** 除当前 Workspace 与平台批准的技能/引擎挂载外不存在额外可写宿主路径

#### Scenario: Claude Code runtime 使用 bridge 网络

- **WHEN** 冻结契约选择 `coding_runtime=claude_code` 且 feature flag 允许该 runtime
- **THEN** 适配器以 `network_mode=bridge` 记录并使用该 Sandbox
- **AND** 仍拒绝 privileged、Docker socket 与额外宿主挂载

#### Scenario: Claude Code 使用持久 Sandbox 身份

- **WHEN** 有效 Claude Code run 启动
- **THEN** Claude Code 在绑定 Sandbox 内执行，并可在权限允许时自行安装依赖
- **AND** 容器不得获得 privileged、Docker socket 或宿主机访问

#### Scenario: legacy runtime 不得获得网络

- **WHEN** Code run 使用 `coding_runtime=legacy` 或未选择 Claude Code
- **THEN** 适配器以 `network_mode=none` 记录
- **AND** Manifest 不得单独把 legacy run 改成 `bridge`

#### Scenario: 请求未批准网络或宿主挂载

- **WHEN** Manifest、任务或工具请求 Docker socket、宿主路径，或请求 `host` 网络
- **THEN** 系统在适配器启动或动作执行前拒绝请求
- **AND** 记录不可被任务设置覆盖的策略拒绝

### Requirement: 可信 runner 镜像必须预装 Claude Code、OpenSpec CLI 与 SOP 技能

支持 CodeAgent 的可信 runner 镜像 SHALL 在构建时安装固定版本的 Claude Code CLI、固定版本的 OpenSpec CLI，以及 propose/apply/verify/archive 与 planning-with-files 技能文件（含规划脚本）。镜像 MUST NOT 包含 grill-me。运行时 MUST NOT 通过网络动态安装这些组件。

生产可信镜像 SHOULD 使用 registry `image@sha256:<manifest-list-digest>` 形式发布并信任 multi-arch manifest list，以同时覆盖 `linux/arm64` 与 `linux/amd64`。本地测试 MAY 使用裸 `sha256:<digest>` fixture，但生产 Manifest UI SHOULD 引导使用完整 image reference。

#### Scenario: 镜像含固定编码工具链

- **WHEN** 管理员按 digest 构建并信任 runner 镜像
- **THEN** 镜像内可执行固定版本的 `claude` 与 `openspec`
- **AND** SOP 技能文件存在于镜像约定路径
- **AND** 不存在 grill-me 技能目录

#### Scenario: 禁止运行时安装

- **WHEN** Claude Code 或 OpenSpec CLI 在启动后缺失
- **THEN** 系统以 `runtime_unavailable` fail closed
- **AND** 不得在容器内 `npm install` 或从宿主机 bind 用户插件目录

#### Scenario: 旧 runner digest 不再用于新 run

- **WHEN** 旧 runner digest 未预装 Claude Code 或不再出现在平台 trusted digest 配置
- **THEN** 新 Manifest 发布或新 run admission 不得使用该 digest
- **AND** 历史已发布 Manifest 不被后台改写，只显示为不可运行配置

#### Scenario: multi-arch 生产镜像使用 manifest list digest

- **WHEN** 管理员发布生产 runner 镜像
- **THEN** 可信 digest 使用 registry `image@sha256:<manifest-list-digest>`
- **AND** 该 manifest list 覆盖 `linux/arm64` 与 `linux/amd64`

### Requirement: Claude Code workspace 必须是正常 Git worktree

当 run 使用 `coding_runtime=claude_code` 时，挂载进 runner 的 `/workspace` SHALL 包含工作树文件和真实目录形式的 `/workspace/.git/`，使 `git -C /workspace status`、`diff` 与 `log` 可直接工作。该 `.git` MUST 来自 sealed sanitized snapshot，MUST NOT 包含 remotes、hooks、alternates 或凭据。平台 MAY 保留 `run_root/source.git` 作为兼容入口，但它 MUST 指向或等价于同一份 run-local sanitized metadata，不得形成两套可漂移 Git metadata。

Workspace baseline、changed paths、Verifier 与 Sealer patch MUST ignore `.git` metadata. Claude Code 和平台工具 MUST NOT create commits; any HEAD/ref movement from the frozen commit SHALL fail workspace integrity before seal.

#### Scenario: Claude Code runner 挂载正常 Git repo

- **WHEN** `claude_code` run 完成 Workspace 准备并启动 runner
- **THEN** `/workspace/.git/` 是真实目录
- **AND** `git -C /workspace status` 能在容器内执行
- **AND** `run_root/source.git` 不形成第二份独立 Git metadata

#### Scenario: Git metadata 不进入 patch

- **WHEN** Claude Code 或 Git 命令更新 `.git` 内部状态
- **THEN** Workspace changed paths 和 sealed patch 不包含 `.git/**`
- **AND** Verifier/Sealer 仍只评估业务文件变化

#### Scenario: 禁止 Claude Code 推进 Git history

- **WHEN** Claude Code、Code Tool 或 shell 命令尝试 `git commit` 或移动 HEAD/ref
- **THEN** 平台应在 shell policy 或 integrity verification 阶段拒绝
- **AND** run 不得进入 `patch_ready`

### Requirement: 运行资源限制必须取所有层级的最严格值

runner 的 CPU、内存、进程数、磁盘、执行时间和输出限制 SHALL 取平台、组织、项目、Manifest、Profile 与任务各层有效值中的最严格值。达到任一限制时系统 SHALL 停止新的工具动作，保留可允许的审计事实，并以稳定原因结束或取消 run。

#### Scenario: Manifest 预算高于平台上限

- **WHEN** Manifest 请求的资源预算高于平台硬上限
- **THEN** 有效 runner 配置使用平台上限
- **AND** 审计记录请求值、有效值和限制来源

#### Scenario: runner 达到资源限制

- **WHEN** runner 超过冻结的 CPU、内存、进程、磁盘、时间或输出限制
- **THEN** 系统停止容器与后续工具动作并标记对应的 `budget_exhausted` 或 `resource_limit_exceeded`
- **AND** 不把未完成验证的修改标记为 `patch_ready`

### Requirement: Docker 部署必须验证 Workspace bind path 映射

当 API 运行在容器内并通过外部 Docker daemon 启动 runner 时，系统 SHALL 将 API 容器可见的 Workspace 路径显式映射为 daemon 主机可见且位于批准根目录内的 bind path。启动前 SHALL 验证映射存在、规范化后未逃逸、指向预期 run Workspace 且以预期读写模式挂载；映射无效时不得启动 runner。API 可见 Workspace 路径 MUST 位于与 Workspace 物化相同的配置 API root（或规范 fallback）之下；因物化根与映射根不一致而导致路径无法通过 containment 校验时，系统 MUST 以 `workspace_mount_invalid` fail closed。

#### Scenario: Compose 环境正确挂载 Workspace

- **WHEN** API 容器内 Workspace 路径具有有效的 daemon 主机路径映射
- **THEN** runner 在约定容器路径看到同一 run 的源码与写入结果
- **AND** 真实 Docker 集成验证可证明 API、主机与 runner 三方路径身份一致

#### Scenario: Compose 映射缺失或错误

- **WHEN** 主机路径映射缺失、不存在、逃逸批准根目录或指向其他 run
- **THEN** 系统以 `workspace_mount_invalid` 终止准备
- **AND** 不使用空目录、API 容器路径或猜测路径启动 runner

#### Scenario: Workspace 物化根与映射 API root 不一致

- **WHEN** 已准备的 Workspace 路径不在当前配置的 Workspace API root（或规范 fallback）之下
- **THEN** 系统在启动 runner 前以 `workspace_mount_invalid` 终止
- **AND** 不把该路径作为 Docker bind source 交给 daemon

### Requirement: Local 发布 runner 必须提供固定最小工具链

local 发布 SHALL 复用绑定 Sandbox 的现有镜像。镜像可预装 dbt、jq、yq、`clickhouse client` 等工具，但这些业务工具 MUST NOT 成为 Runtime 启动必备项。缺失工具时 Claude Code MAY 在 Sandbox 权限允许时自行安装；缺失不得阻塞 Workspace 或 Runtime 启动。

#### Scenario: 双架构镜像通过 preflight

- **WHEN** local 发布任务在已运行的绑定 Sandbox 上启动
- **THEN** Runtime 不因可选业务工具缺失而 fail closed
- **AND** 缺失依赖不阻止 Workspace 或 Claude Code 启动

### Requirement: Local 发布网络与权限必须受限

local 发布 SHALL 使用与普通 Claude Code 任务相同的持久 Sandbox 边界：禁止 privileged、Docker socket 和宿主挂载；Claude Code 使用 `bridge` 网络。系统 MUST NOT 要求 Manifest 声明发布目的地，也 MUST NOT 为此单独降权到非 root 一次性 runner。

#### Scenario: 访问未授权网络

- **WHEN** 发布进程需要外网且冻结契约为 `coding_runtime=claude_code`
- **THEN** 任务使用绑定 Sandbox 的 `bridge` 网络
- **AND** 仍不得挂载 Docker socket 或切换到 `host` 网络
