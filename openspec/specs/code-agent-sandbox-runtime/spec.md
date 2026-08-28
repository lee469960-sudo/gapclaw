# code-agent-sandbox-runtime Specification

## Purpose

定义 CodeAgent Code Tools 的单一容器执行边界、部署路径映射、运行时加固和资源生命周期，使代码操作不能退回 API 进程或越过 Workspace。

## Requirements

### Requirement: 所有 Code Tools 必须在每 run 专用容器内执行

`read`、`search`、`edit`、`git`、`shell` 和 `test` 能力 SHALL 仅在绑定该 Code run 的专用 runner 容器内执行。API 进程 SHALL 只执行鉴权、策略计算、调度、审计和工件协调，MUST NOT 直接读取、搜索、修改或执行 Workspace 内容；runner 不可用时系统 MUST fail closed，不得回退宿主执行。

#### Scenario: 执行读取到测试的工具序列

- **WHEN** CodeAgent 在有效 run 中调用任一允许的 Code Tool
- **THEN** 调用在该 run 的同一专用 runner 边界和冻结 Workspace 内执行
- **AND** 审计事实记录 run、容器、工具、策略版本和结果

#### Scenario: runner 启动失败

- **WHEN** runner 镜像不可用、digest 不匹配或容器无法安全启动
- **THEN** 系统以稳定的基础设施或策略原因终止准备
- **AND** 不在 API 进程或其他容器中重试该 Code Tool

### Requirement: Claude Code 必须运行在现有 run 专用 Sandbox 内

当 CodeAgent 选择 `coding_runtime=claude_code` 时，系统 SHALL 在该 run 的现有专用 runner 容器内启动 Claude Code。系统 MUST NOT 为 Claude Code 创建独立 Sandbox、MUST NOT 在 API 进程或宿主机执行 Claude Code，也 MUST NOT 绕过现有 Workspace bind path、runner image digest、网络、filesystem、shell 和资源限制。

#### Scenario: 在现有 runner 中启动 Claude Code

- **WHEN** 有效 CodeAgent run 使用 `claude_code` runtime
- **THEN** Claude Code 进程在该 run 现有专用 runner 容器内启动
- **AND** working directory 指向该 run 已准备好的 repository Workspace
- **AND** API 进程只负责调度、策略、审计和工件协调

#### Scenario: 禁止创建第二套 Sandbox

- **WHEN** runtime adapter 准备启动 Claude Code
- **THEN** 系统不得创建与当前 run runner 并行的第二个可写 Sandbox
- **AND** 不得从宿主机用户目录或 API 文件系统暴露未授权文件给 Claude Code

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

runner SHALL 使用管理员批准且按 digest 固定的镜像、非特权身份、只读 root filesystem、全部 capability drop、`no-new-privileges` 和默认无网络配置。仅该 run 的 Workspace 可写；Docker socket、Secret Store、API 文件系统、其他 run Workspace 和未批准宿主路径 MUST NOT 被挂载或暴露。任何网络例外 SHALL 同时获得平台和已发布 Manifest 明确授权。

当冻结契约为 `coding_runtime=claude_code` 时，平台授权的网络例外 SHALL 为 Docker `bridge`（容器可访问公网）。该例外 MUST NOT 被描述为域名 allowlist。`coding_runtime=legacy` 或未选择 Claude Code 时，runner MUST 保持 `network_mode=none`。`host` 及其他非 `none`/`bridge` 模式 MUST 被拒绝。

#### Scenario: 默认启动隔离 runner

- **WHEN** 有效 Code run 启动且未声明获批网络例外
- **THEN** runner 以固定镜像 digest、只读 rootfs、无额外 capabilities、禁止提权和无网络方式运行
- **AND** 除当前 Workspace 外不存在可写挂载

#### Scenario: Claude Code runtime 使用 bridge 网络

- **WHEN** 冻结契约选择 `coding_runtime=claude_code` 且 feature flag 允许该 runtime
- **THEN** runner 以 `network_mode=bridge` 启动
- **AND** 其余隔离项（非特权、只读 rootfs、cap-drop ALL、no-new-privileges、单 Workspace 挂载）保持不变

#### Scenario: legacy runtime 不得获得网络

- **WHEN** Code run 使用 `coding_runtime=legacy` 或未选择 Claude Code
- **THEN** runner 以 `network_mode=none` 启动
- **AND** Manifest 不得单独把 legacy run 改成 `bridge`

#### Scenario: 请求未批准网络或宿主挂载

- **WHEN** Manifest、任务或工具请求未获平台批准的网络目标、Docker socket、宿主路径，或请求 `host` 网络
- **THEN** 系统在容器启动或动作执行前拒绝请求
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

### Requirement: runner 容器必须在所有终态立即移除

系统 SHALL 在成功、验证失败、策略拒绝、取消、超时、预算耗尽或基础设施失败后停止并立即移除该 run 的 runner 容器及临时网络。清理失败 SHALL 产生可观察且可重试的清理记录，容器不得继续接受工具动作。

#### Scenario: run 正常或异常结束

- **WHEN** Code run 进入任一终态
- **THEN** 系统撤销 runner 执行资格并立即请求停止和移除容器及临时网络
- **AND** Workspace 是否保留由独立保留策略决定

#### Scenario: 容器删除失败

- **WHEN** runner 停止或删除操作失败
- **THEN** 系统记录 `sandbox_cleanup_failed` 并触发受控重试或告警
- **AND** 不将残留容器视为可运行资源
