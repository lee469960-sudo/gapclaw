## ADDED Requirements

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
