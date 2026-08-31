## MODIFIED Requirements

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
