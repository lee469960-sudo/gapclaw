## Why

标准 local 发布引入了平台档案、命令注册表和专用确认流程，超过了用户需要的最小工作流。当前运行代码已经把 local 发布收成：在 Agent 绑定的持久 Sandbox 内直接进入 Claude Code，由仓库 CI/脚本决定下一步。

## What Changes

- **BREAKING** 移除标准 local 发布的平台项目档案、命令注册表和专用确认流程；Manifest 不含发布字段。
- local 发布与其他 CodeAgent 任务一样，在已绑定的持久 Sandbox 中直接进入 Claude Code Runtime。
- Claude Code 自行阅读 Workspace 的 CI/脚本并在 Sandbox 权限允许时安装缺失依赖；平台不提供 lockfile→run-local 安装桥，也不设 `local_publish_entry_not_found` 门禁。
- 保持现有 Workspace、持久 Sandbox、Repository、Skill、MCP、Verifier、Sealer、审计和最终结果结构，不新增控制面或发布配置页面。

## Capabilities

### New Capabilities

- `code-agent-minimal-local-publish`: 无平台发布档案的 Claude Code local 发布发现与执行闭环。

### Modified Capabilities

- `code-agent-sandbox-runtime`: Claude Code 任务复用绑定 Sandbox；禁止 privileged、Docker socket 与宿主挂载；不要求一次性 runner 的 root 引导/降权。
- `code-agent-coding-runtime`: Claude Code local 发布任务从仓库发现入口；缺失工具由 Claude 在 Sandbox 内自行安装。
- `code-agent-conversation-progress`: local 发布使用与普通 CodeAgent 任务相同的中文过程事件，不设平台发布阶段。
- `code-agent-control-plane`: Manifest 不得声明或消费 local 发布命令/确认策略。
- `code-agent-local-publish`: 取消 canonical 命令、preflight 确认和 Manifest 网络例外；改为仓库驱动、Verifier 收尾。
- `code-agent-project-policy`: local 发布策略改为持久 Sandbox 边界，不再依赖 Manifest 发布锁。
- `code-agent-verification`: local 发布仍由 Verifier 裁决，不接受模型文本单独成功。

## Impact

- 删除 `CODE_STANDARD_LOCAL_PUBLISH_PROFILES` 和 `CODE_LOCAL_PUBLISH_COMMANDS` 的新 Run 依赖。
- Manifest API/UI 继续不暴露 local 发布字段；历史数据库列仅兼容。
- 主 spec 中仍描述 Manifest canonical 发布的条款由本 change 覆盖为当前实现。
