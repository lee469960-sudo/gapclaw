## Context

现有 CodeAgent 已有 Task、Repository、Workspace、Sandbox、Manifest、Skill、MCP、Verifier、Sealer 和 run/result 管理。本设计只在现有链路中增加 Claude Code Coding Runtime，不重建控制面或执行隔离。

关键约束：

- Claude Code 必须运行在现有每 run 专用 runner 容器中。
- Existing CodeAgent Verifier/Sealer 仍是最终成功裁决路径。
- Skill/MCP 权限继承现有 Agent/Manifest 授权，不开放全局能力。
- MVP 正式模型路径只支持 Cloud Claude；Local Model/Gateway 作为后续实验能力。

## Goals / Non-Goals

**Goals:**

- 增加 `coding_runtime=claude_code` feature flag / Manifest 选择。
- 新增 `ClaudeCodeRuntimeAdapter`，封装 Claude Code CLI 启动、session、事件、transcript、预算和错误分类。
- 将绑定 Skill 和授权 MCP 转换为 run-local Claude Code 配置。
- 增加 runtime preflight，证明 sandbox image、配置、cwd、MCP、模型和预算控制可用。
- 将 Claude Code coding completed 接入现有 Verifier/Sealer，并支持默认最多 2 次同 session 自修复。
- 通过统一事件流展示 runtime 启动、skill/mcp 加载、工具调用、文件变化、测试、retry、verification 和 artifact 状态。
- 支持关闭 feature flag 后新 run 回到 legacy CodeAgent runtime。

**Non-Goals:**

- 不重做 Task、Repository、Workspace、Sandbox、Manifest、Verifier、Sealer 或 Result Management。
- 不把 Claude Code 做成普通 Code Tool，也不保留双层 coding planner/replanner。
- 不在本次实现 Local Model 正式路径、完整 LiteLLM/Gateway 路由或本地模型 benchmark。
- 不做完整 Claude Code 设置中心。
- 不开放未绑定 Skill、未授权 MCP、未批准网络或宿主机配置目录。
- 不支持自动 git commit、push、PR。

## Decisions

### 1. Claude Code 是 Coding Runtime，不是 Tool

`claude_code` 被建模为 CodeAgent 的 runtime backend。CodeAgent 负责准备冻结上下文和执行边界，Claude Code 负责细粒度 coding loop。

Alternatives considered:

- Claude Code 作为普通 tool：会让 CodeAgent planner/replanner 与 Claude Code planner/replanner 叠加，产生双层 loop。
- 只把 read/edit/bash/test 委托给 Claude Code：会保留外层细粒度调度，不能显著提升长任务和自修复能力。

### 2. 单一 `ClaudeCodeRuntimeAdapter`

新增 adapter 作为唯一集成点：

```text
CodeAgent Orchestrator
  -> ClaudeCodeRuntimeAdapter.run(input)
  -> Claude Code CLI in runner
  -> ClaudeCodeRuntimeResult
  -> Existing Verifier/Sealer
```

Adapter 输入来自冻结 run contract，输出只包含编码阶段事实，不直接决定 `patch_ready`。

### 3. 使用现有 Sandbox，不新增 Sandbox

Claude Code 作为进程运行在现有 runner 容器内，工作目录指向当前 run 的 repository Workspace。runner image 需要预装 Claude Code CLI，并通过 digest 固定。

Preflight 至少检查：

- `claude --version`
- run-local config dir writable
- repository cwd readable/writable
- MCP config parseable
- Cloud Claude model endpoint reachable
- budget watchdog active

### 4. run-local Skill/MCP Adapter

Skill Adapter：

- 读取当前 Agent 已绑定/已授权 Skill。
- 复制或渲染为 run-local `.claude/skills/<skill>/SKILL.md`。
- 可携带脚本/资源，但必须位于 run-local 目录，并受 allowed paths、secret scan 和执行策略约束。
- 启动事件展示 skill 名称、来源、版本/hash。

MCP Adapter：

- 读取当前 Agent/Manifest 已授权 MCP。
- 生成 run-local MCP config。
- 凭证通过 sandbox secret/env 注入，配置文件不得保存明文 secret。
- 启动事件展示 MCP server、授权状态和工具数量。

### 5. Verifier retry loop 由 CodeAgent 控制

Claude Code 完成后：

```text
coding_completed
  -> Existing Verifier
  -> PASS -> Existing Sealer -> patch_ready
  -> FAIL -> feedback to same Claude Code session
```

默认 retry 策略：

- 初次 verifier attempt + 最多 2 次修复 retry。
- retry 使用同一 Claude Code session、同一 Workspace、同一冻结 policy。
- retry feedback 必须脱敏，只包含失败命令、失败摘要、相关文件、policy violation 和 verifier facts。
- retry 上限或预算耗尽后进入稳定非成功终态。

### 6. Event stream 做稳定语义，不透传 raw output

Adapter 将 Claude Code 过程转换成 CodeAgent 统一事件：

- `runtime_started`
- `skill_loaded`
- `mcp_loaded`
- `tool_call`
- `file_changed`
- `test_run`
- `verifier_failed_retrying`
- `verifier_passed`
- `artifact_sealed`

Raw output/transcript 不直接进入前端。用户可见内容必须 redaction。

### 7. MVP 模型路径先 Cloud Claude

MVP 只实现 Cloud Claude 正式路径。可以在数据结构中预留 `model_config`/`gateway_config` 字段，但不承诺 Local Model production。

Local Model 后续需要独立 benchmark gate：

- 代码理解
- 多文件修改
- tool calling
- test-fix loop
- 长任务稳定性
- 错误恢复
- context management

### 8. 回滚必须是配置级

通过 feature flag 或 Manifest runtime 选择关闭 Claude Code。关闭后新 run 使用 legacy CodeAgent runtime；历史 run、transcript、audit、Verifier report 和 artifact 保留。

## Risks / Trade-offs

- Claude Code CLI 版本漂移 → runner image 固定 digest，并在 preflight 记录 CLI version。
- Cloud 模型不可用或费用失控 → 明确 `model_unavailable`，预算由 CodeAgent Manifest 控制。
- Skill/MCP 注入扩大权限 → 只注入当前授权集合，run-local 配置不写明文 secret。
- Transcript 泄露 secret-like 内容 → event/transcript redaction + 现有 source/patch/artifact scanner。
- 双层 loop 复发 → CodeAgent 禁止在 `claude_code` runtime 下执行细粒度 coding replanner。
- Retry 吞预算 → 默认最多 2 次 retry，且受时间、工具调用、资源预算限制。
- UI 看不到过程 → runtime event visibility 作为 MVP 验收项。

## Migration Plan

1. 增加 runtime enum/feature flag，默认 legacy。
2. 构建包含 Claude Code CLI 的 trusted runner image，并固定 digest。
3. 实现 adapter preflight 与失败分类，但先只做 dry-run/preflight 测试。
4. 接入 run-local Skill/MCP config 生成。
5. 接入 Cloud Claude 模型配置注入。
6. 接入 coding loop、event streaming 和 transcript redaction。
7. 接入 Existing Verifier/Sealer 与 retry loop。
8. 在 UI 暴露 runtime 选择与过程事件。
9. 通过 MVP 验收用例后才允许项目级启用。

Rollback:

- 关闭 `claude_code` feature flag 或将 Manifest runtime 改回 legacy。
- 不删除历史 Claude Code run 和 artifact。
- 若 image/preflight 失败，新 run fail closed，不回退到宿主执行。

## Open Questions

- Claude Code CLI 在 runner image 中的具体版本和升级节奏可在实现阶段按平台发布策略确定。
- Local Model benchmark 的具体评分阈值不属于本 change，后续单独定义。
