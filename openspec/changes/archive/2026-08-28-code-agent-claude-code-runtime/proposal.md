## Why

现有 CodeAgent 已具备 Task、Repository、Sandbox、Skill、MCP、Manifest、Verifier 与结果管理，但代码理解、多文件修改、调试、测试修复循环和长任务执行能力不足。该变更引入 Claude Code 作为受控 Coding Runtime，以增强现有 CodeAgent 的编码循环，而不是重建 CodeAgent 架构。

## What Changes

- 增加 `claude_code` Coding Runtime 选择，默认仍使用现有 CodeAgent runtime。
- 新增 `ClaudeCodeRuntimeAdapter`，在现有 Agent Sandbox 与现有 Repository Workspace 内启动 Claude Code。
- 将当前 Agent 已授权的 Skill/MCP 通过 run-local 配置注入 Claude Code，不开放全局 Skill/MCP。
- 将 Claude Code coding completed 与 CodeAgent task completed 明确分离；最终成功仍由现有 Verifier 与 Sealer 裁决。
- 增加 Verifier fail 后同 Claude Code session 的受控自修复闭环，默认最多 2 次 retry。
- 增加 runtime preflight、事件流、skill/mcp 加载可见性、transcript redaction 与失败分类。
- MVP 仅支持 Cloud Claude 正式路径；Local Model/Gateway 作为后续 benchmark-gated 能力，不进入本次正式路径。
- 不重做 Task、Repository、Sandbox、Manifest、Verifier、Sealer 或完整 Claude Code 设置中心。

## Capabilities

### New Capabilities

- `code-agent-coding-runtime`: 定义 CodeAgent 如何选择并运行外部 Coding Runtime，尤其是 Claude Code adapter、输入输出契约、事件、preflight、session 与失败分类。

### Modified Capabilities

- `code-agent-profile`: Code Profile 增加 `coding_runtime=claude_code` 显式选择与 legacy 默认兼容要求。
- `code-agent-project-policy`: Manifest/policy/budget 增加 runtime 选择、模型路径、预算与 kill switch 约束。
- `code-agent-sandbox-runtime`: Claude Code 必须运行在现有每 run 专用 runner/sandbox 内，并继承文件系统、网络、shell 与资源边界。
- `code-agent-verification`: Claude Code 完成不等于任务完成；Verifier fail 触发受控同 session retry，最终仍由现有 Verifier/Sealer 裁决。
- `code-agent-operator-ui`: UI 必须展示 runtime 选择、runtime 过程、skill/mcp 加载与 retry/verification/artifact 状态。

## Impact

- Affected backend: CodeAgent runtime orchestration, sandbox image/preflight, policy merge/freeze, skill/MCP injection, event pipeline, verifier retry loop, audit/result models.
- Affected frontend: Agent/Profile/Manifest runtime selection, CodeAgent run process visibility, skill/MCP loaded state and failure reason display.
- Affected deployment: trusted runner image must include pinned Claude Code CLI/version/digest and Cloud Claude credential injection path.
- New dependency: Claude Code CLI inside the trusted runner image; Cloud Claude credentials/config for MVP.
- Non-goals: no new sandbox architecture, no direct git push/commit, no replacement of existing verifier/sealer, no local model production support in this change.
