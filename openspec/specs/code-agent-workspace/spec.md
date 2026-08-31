# code-agent-workspace Specification

## Purpose

定义 CodeAgent 为每次运行准备的受管源码工作区，确保源码基线、写入隔离、完整性与清理均可验证和追溯。

## Requirements

### Requirement: 每个 CodeAgent run 使用独立且固定基线的 Workspace

系统 SHALL 为每个 Code Project 维护可挂载到其绑定持久 Sandbox 的受管 Workspace，并在 Run 启动时通过绑定 Sandbox 将 Manifest 中的 Git 仓库同步到该 Workspace。任务 MAY 在该持久 Workspace 上连续执行；系统 MUST NOT 在每次任务结束时删除 Workspace 或 Sandbox。并发任务 MUST NOT 同时写入同一项目 Workspace。

#### Scenario: 两个 run 操作同一仓库
- **WHEN** 两个任务请求写入同一项目 Workspace
- **THEN** 系统只允许一个任务持有写锁
- **AND** 另一个任务拒绝或等待

#### Scenario: 固定基线准备
- **WHEN** 同一项目在同一持久 Sandbox 上执行后续任务
- **THEN** 系统在同一 Workspace 内 clone 或 fetch Manifest 配置的仓库和 ref
- **AND** 后续任务看到此前保留的工具环境和 Workspace 文件状态
- **AND** 左侧预览继续指向该项目 Workspace

#### Scenario: API 进程尝试源码操作
- **WHEN** API 进程需要执行仓库命令
- **THEN** 系统通过绑定 Sandbox 调度该操作
- **AND** API 进程不得直接在宿主 Workspace 中执行命令

#### Scenario: 同一 Workspace 已有写入任务
- **WHEN** 新任务尝试写入已有活动任务的项目 Workspace
- **THEN** 系统拒绝或排队该新任务并说明 Workspace 正忙
- **AND** 不并发修改同一份代码

### Requirement: Workspace 必须物化到配置的 API root

系统 SHALL 将每个 CodeAgent run 的可写 Workspace 物化到部署配置的 Workspace API root 之下，路径布局为 `<api_root>/<run_id>/workspace`。当 API root 已配置时，系统 MUST NOT 使用与该配置无关的隐式目录名物化 Workspace。当 API root 未配置时，系统 SHALL 使用 `{data_dir}/code-agent/runs` 作为唯一规范 fallback，且 MUST NOT 使用 `{data_dir}/code_agent/runs` 或其他别名。该物化根 MUST 与后续 Sandbox bind 映射所校验的 API 可见根为同一权威根。

#### Scenario: 已配置 API root 时准备 Workspace

- **WHEN** 部署已配置 Workspace API root，且 CodeAgent run 开始准备 Workspace
- **THEN** Workspace 被创建在该 API root 下的 `<run_id>/workspace`
- **AND** 系统不在其他隐式目录下创建该 run 的可写 Workspace

#### Scenario: 未配置 API root 时使用规范 fallback

- **WHEN** Workspace API root 未配置且 CodeAgent run 开始准备 Workspace
- **THEN** 系统使用 `{data_dir}/code-agent/runs/<run_id>/workspace` 作为物化路径
- **AND** 不使用 `{data_dir}/code_agent/runs` 或其他未文档化别名

### Requirement: Workspace 完整性异常必须阻止交付

系统 SHALL 在准备完成、写入期间和封存前校验 Workspace 的快照身份、基线与受控状态。检测到快照或 commit 不一致、无法解释的外部写入、跨 run 访问、未受控 checkout 状态、mount 指向错误或其他完整性异常时 SHALL 停止该 run，标记 `workspace_integrity_error`，且不得交付 patch。

#### Scenario: Workspace 被外部修改

- **WHEN** 系统检测到 Workspace 出现非该 run runner 可解释的文件写入
- **THEN** 系统停止后续代码执行并标记 `workspace_integrity_error`
- **AND** 不生成可接受的 patch 工件

#### Scenario: Workspace 基线或挂载身份不匹配

- **WHEN** Workspace 的快照、commit、run 标识或实际挂载路径与冻结契约不一致
- **THEN** 系统在工具执行前或检测后立即阻止后续动作
- **AND** 不尝试通过重新 checkout 移动 ref 来修复基线

### Requirement: 终止后必须清理可写 Workspace

系统 SHALL 在正常完成、取消、超时、基础设施失败或策略拒绝后立即撤销该 Workspace 的执行资格并移除 runner。Workspace 默认 SHALL 转为不可执行的受保护保留态 7 天用于审计与故障分析，随后按平台与项目保留策略中更严格的期限清理；策略要求立即清理时 SHALL 立即删除。封存工件和审计记录 SHALL 使用独立保留策略，未成功封存的内容不得作为交付物。

#### Scenario: 用户取消运行

- **WHEN** 用户取消正在运行的 CodeAgent 任务
- **THEN** 系统阻止新的 Workspace 动作、移除 runner 并将 Workspace 转为不可执行保留态或按策略立即清理
- **AND** 未封存内容不得被标记为可交付 patch

#### Scenario: Workspace 到期清理

- **WHEN** 不可执行 Workspace 达到其有效保留期限
- **THEN** 系统清理源码和临时数据并记录清理事实
- **AND** 不删除仍在独立保留期内的 sealed artifact、Verifier 报告或审计记录

#### Scenario: 准备或启动阶段基础设施失败

- **WHEN** Workspace 创建后、runner 启动期间或进入统一执行循环前发生基础设施失败
- **THEN** 系统撤销该 run 的所有可写执行能力，并按有效保留策略清理或转为不可执行保留态
- **AND** 未完成的清理或状态转换必须产生可观察失败，而不得保持为可运行的 `pending` 状态

### Requirement: claude_code 新 run 的 /workspace 必须是业务 repo root

当 run 使用 `coding_runtime=claude_code` 时，系统 SHALL 使 runner 内 `/workspace` 成为业务 repository root。若冻结 snapshot 根目录除 sanitized `.git` 与 runtime ignored 文件外仅包含一个普通目录，系统 SHALL 在准备新 run Workspace 时剥离该单一顶层目录并将其内容平铺到 `/workspace`。若 snapshot 根目录包含多个业务顶层条目，系统 MUST NOT 自动剥离目录。历史 run Workspace MUST NOT 被迁移或改写。

#### Scenario: 单顶层目录 snapshot 被平铺

- **WHEN** snapshot 根目录仅包含 `.git` 与一个普通业务目录 `gamestat`
- **AND** 新 run 使用 `coding_runtime=claude_code`
- **THEN** runner 内 `/workspace` 直接包含 `gamestat` 目录内的业务文件
- **AND** `/workspace` 是业务 repo root
- **AND** 用户可见路径不额外要求 `gamestat/` 前缀

#### Scenario: 多顶层 snapshot 不自动剥离

- **WHEN** snapshot 根目录包含多个业务顶层目录或文件
- **AND** 新 run 使用 `coding_runtime=claude_code`
- **THEN** 系统保持 snapshot 结构，不自动选择某个目录作为 repo root
- **AND** 若无法确定业务 repo root，则以稳定 workspace readiness 原因阻止进入 coding loop

#### Scenario: 历史 run 不迁移

- **WHEN** 系统升级后存在旧 run Workspace
- **THEN** 系统不修改该历史 Workspace 的目录结构或 Git metadata
- **AND** 历史 run 仍按原始审计事实展示

### Requirement: claude_code /workspace 必须包含来自 snapshot 的 sanitized Git metadata

当 run 使用 `coding_runtime=claude_code` 时，系统 SHALL 从冻结 snapshot 的 sanitized Git metadata 准备 `/workspace/.git`，并验证 `/workspace` 的 Git top-level 等于 `/workspace`。系统 MUST NOT 在 run 阶段重新 clone 原始仓库或从外部 Git 服务获取 Git metadata。若 Git metadata 缺失、top-level 指向错误目录或 HEAD/ref 无法与冻结基线对应，系统 SHALL 在 coding loop 前 fail closed。

#### Scenario: Git top-level 正确

- **WHEN** `claude_code` 新 run 完成 Workspace 准备
- **THEN** `/workspace/.git` 存在且来自 sanitized snapshot
- **AND** `git -C /workspace rev-parse --show-toplevel` 指向 `/workspace`
- **AND** `git -C /workspace status` 可用于 Claude Code 上下文

#### Scenario: 禁止 run 阶段重新 clone

- **WHEN** Workspace Git metadata 缺失或异常
- **THEN** 系统不得通过重新 `git clone` 修复该 run
- **AND** 返回稳定 workspace readiness 失败原因
