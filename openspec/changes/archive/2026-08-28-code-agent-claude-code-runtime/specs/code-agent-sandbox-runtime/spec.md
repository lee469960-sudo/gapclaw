## ADDED Requirements

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
