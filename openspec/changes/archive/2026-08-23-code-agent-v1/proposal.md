## Why

现有所有 Agent 都运行在统一 ReAct Runtime 中，适合业务任务自动化，但缺少对代码修改所需的受管工作区、隔离执行、代码工具和强制验证。直接为编码场景复制一套运行时会破坏既有任务、权限和审计链路；现在需要以受控方式引入可审阅的 CodeAgent V1。

## What Changes

- 在统一 Runtime 中新增显式 `code` Profile；未显式选择该 Profile 的 Standard Agent 行为、工具路由和终态保持不变。
- 为 Code Profile 引入受管、按 run 隔离的 Workspace，以及面向受管内部非生产仓库的固定基线 checkout。
- 提供受策略约束的代码读取、搜索、编辑、测试和受限 shell 工具；默认断网，不提供自动 Git 写入、真实秘密或任意动态工具。
- 在交付前强制执行独立 Verifier，并封存由 Workspace 生成的 canonical diff、验证报告和可审计工件；只有完整通过策略与验证的结果可成为 `patch_ready`。
- 新增项目级 Code Manifest、Profile/策略与运行边界，以约束允许路径、测试命令、镜像、工具和资源预算。
- 增加 Code Profile 专属的可观察阶段和安全终态，同时保持统一任务、事件、权限、预算及审计契约。

## Capabilities

### New Capabilities

- `code-agent-profile`: 定义 Code Profile 的显式选择、默认兼容性、任务边界和受限代码工具能力。
- `code-agent-workspace`: 定义按 run 隔离、按固定 commit 创建和完整性校验的受管代码工作区。
- `code-agent-verification`: 定义强制验证、canonical patch 封存、交付条件和 CodeAgent 专属终态。
- `code-agent-project-policy`: 定义项目 Manifest、权限/资源/Sandbox 策略、V1 非目标和策略拒绝行为。

### Modified Capabilities

- `agent-runtime`: 扩展统一 Runtime 以在入口解析 Profile、保持 Standard Agent 默认行为不变，并承载 Code Profile 的统一事件、预算、取消和审计生命周期。
- `agent-config`: 扩展 Agent 配置以声明可选 Profile，并在未声明时保持既有 Standard Profile 语义。

## Impact

- 影响 `apps/api/app/services/agent_runtime/` 的运行入口、工具调度、事件、checkpoint/终结和验证编排边界。
- 影响 Agent 配置、任务 API/持久化和前端任务/运行详情中的可选 Profile 与 CodeAgent 工件展示。
- 新增受管 Workspace、Sandbox runner、项目 Manifest、Code Tools、Verifier 与工件存储集成；V1 仅面向内部、非生产 allowlist 仓库。
- 需要 Standard Agent 兼容性回归、CodeAgent 安全负向测试，以及试点的质量、成本和清理指标。
