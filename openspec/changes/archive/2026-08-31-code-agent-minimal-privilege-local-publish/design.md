## Context

See proposal.md. 当前运行代码已用持久 Sandbox 绑定替代一次性 runner；local 发布不再走平台档案或确认门禁。

## Goals / Non-Goals

**Goals:**

- 移除 local 发布的平台档案、命令注册表、专用凭据绑定和确认门禁。
- 让仓库 CI、脚本成为 local 发布的唯一发现来源。
- 在绑定的持久 Sandbox 内由 Claude Code 执行安装与发布，结果仍由 Verifier 裁决。

**Non-Goals:**

- 不授予 Claude Code Docker socket、特权容器或宿主机访问。
- 不恢复 Manifest 配置发布命令的旧路径。
- 不实现平台 lockfile 扫描或一次性 runner 的 root 引导/降权。

## Decisions

### 持久 Sandbox，而非一次性 runner

Claude Code 在 Agent 编辑页已绑定且运行中的持久 Sandbox 的项目 Workspace 中执行。平台不为此任务创建独立 runner，也不在任务结束时销毁该 Sandbox。

### 直接进入 Claude Code

local 发布不在平台层扫描、猜测或冻结发布入口。它和其他 CodeAgent 任务一样直接进入 Claude Code Runtime。平台不以 `local_publish_entry_not_found` 阻塞会话。

### 依赖由 Claude 在 Sandbox 内自行安装

平台不提供 lockfile→run-local 安装桥。Claude Code 提示词要求在 Sandbox 权限允许时安装最小依赖；被网络或权限阻断时报告并停止。

### 运行后仍由 Verifier 决定成功

发布命令退出成功不是任务成功。现有输出脱敏、Verifier、Sealer、结果和清理继续生效。

## Risks / Trade-offs

- [包安装需要公网] → 沿用 Claude Code `bridge` 网络；安装失败不宣称发布成功。
- [CI 含多个发布路径] → 由 Claude Code 阅读仓库后请求澄清；平台不做文件名猜测。
- [持久 Sandbox 可写] → 仍禁止 privileged、Docker socket 和宿主挂载。
