## Context

See proposal.md. 当前实现把 Workspace、Claude Code 和工具执行绑定到短生命周期 Code Runner，并将大量运行时配置冻结到 Manifest。系统已具备持久 Sandbox 管理、Agent 编辑页 Sandbox/资源配置、Code Workspace 预览与普通 Sandbox 终端。

## Goals / Non-Goals

**Goals:**

- 以现有 Agent Sandbox 绑定替代 Code Runner，提供持久的 Claude Code 开发环境。
- 让 Git Manifest 与 Agent 资源配置各自只负责一个边界。
- 删除本次迭代引入的 local publish 和 runner 特化代码，保持 UI 与运行实际一致。

**Non-Goals:**

- 不自动启动、停止、创建、提权或重新配置用户绑定的 Sandbox。
- 不在 Manifest 添加 Sandbox、模型、Skill、MCP 或部署配置。
- 不为发布、CI、环境部署增加新的控制平面。

## Decisions

### 已绑定 Sandbox 是唯一执行目标

CodeAgent 读取现有 Agent Sandbox 绑定并检查它处于运行状态；通过后将项目 Workspace 挂载到稳定目录，在该环境执行 Claude Code 和代码工具。选择此方案是为了让用户终端、工具安装和 Claude Code 共享状态。每 run runner 保持隔离的替代方案被拒绝，因为工具和环境无法持久化。

### Manifest 只保存 Git 配置

Manifest 只提供仓库来源、凭据引用和请求 Ref；发布时只做字段、授权和来源策略校验，不导入仓库、不扫描源码、不封存 snapshot。Run 启动时通过绑定持久 Sandbox 在项目 Workspace 内执行 `git clone/fetch/checkout`，并把实际 commit 记录到 run。选择该边界是为了让 Manifest 成为轻量 Git 配置，避免发布页承担运行环境和源码物料职责。

### Workspace 使用项目稳定挂载与单写入者

项目 Workspace 是用户可见的持久开发目录，左侧预览读取同一路径。活动 CodeAgent 任务取得项目级写锁；后续写入任务拒绝或排队。选择项目锁而非每 run 副本，避免同一目录被并发写坏。

### 删除发布型流程，保留普通开发事实

运行结果显示 Claude Code 输出、文件变更、测试和普通验证事实；不再要求 release ID、发布证据或 local 发布确认。保留脱敏、项目访问权和基本 Git Workspace 校验。将发布流程作为独立专用系统的替代方案被拒绝，因为它不属于普通编码循环。

## Risks / Trade-offs

- [持久 Sandbox 中工具和文件会累积] → 用户通过现有终端和 Sandbox 管理功能维护环境；项目 Workspace 保持清晰挂载路径。
- [Sandbox 停止造成任务不可运行] → 返回稳定提示并引导用户手动启动，不做隐式基础设施动作。
- [并发编辑同一 Workspace] → 使用项目级单写入者。
- [旧 Manifest/Run 仍含 runner 字段] → 历史记录只读兼容；新任务忽略弃用字段。

## Migration Plan

1. 让新任务解析已有 Sandbox 绑定并准备项目稳定 Workspace。
2. 将 Claude Code 与 Code Tool 调度切换到持久 Sandbox，接通对话与左侧预览。
3. 收敛 Manifest API/UI 至 Git 配置；发布时不导入、不扫描、不封存。
4. Run 启动时在绑定 Sandbox Workspace 内同步 Git 仓库并记录实际 commit。
5. 删除 runner/local publish 专用路径、配置、事件和测试，执行回归。
6. 回滚时恢复上一版本应用；已存在的 Sandbox、Git Workspace 和 Manifest 数据不做破坏性删除。
