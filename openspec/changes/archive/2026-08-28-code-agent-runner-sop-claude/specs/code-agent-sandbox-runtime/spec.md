## MODIFIED Requirements

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

## ADDED Requirements

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
