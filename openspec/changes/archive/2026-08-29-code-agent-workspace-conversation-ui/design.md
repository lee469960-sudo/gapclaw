## Context

See `proposal.md` for motivation. CodeAgent 已有独立 Run、Workspace、Runner、事件事实和结果序列化能力；通用 Agent 则使用 `/workplace`。本 change 只解决 CodeAgent 的可见性和操作入口一致性。

## Goals / Non-Goals

**Goals:**

- 让左侧面板根据当前 Agent/Run 切换到真实 CodeAgent Workspace。
- 复用现有 Workspace 路径校验、Run 授权、脱敏和生命周期策略。
- 将运行事件与最终结果合并到同一对话时间线，支持刷新和断线后恢复。
- 保持成功、失败、目标未找到和需要决策等终态与后端结果一致。

**Non-Goals:**

- 不把 CodeAgent 仓库挂载到通用 `/workplace`。
- 不新增 Sandbox、Runner 或仓库获取实现。
- 不允许前端直接写 Workspace；修改仍通过 CodeAgent 工具和现有 Verifier/Sealer 流程完成。

## Decisions

### 1. Workspace 以 Run 为绑定键

前端使用当前 `code_run_id` 请求 Workspace 元数据和文件列表；后端从 Run 的 `workspace_path` 解析目录，并执行现有路径 containment、Run 权限和运行状态校验。相比按 `sandbox_id` 读取，可避免误读通用 `/workplace`。

### 2. 只读预览 API 复用现有安全边界

新增的文件树、文件内容和 Git 状态接口只提供只读数据，过滤 `.git/**`、`.claude/**`、凭证和二进制内容，并限制单文件大小、递归深度和返回数量。接口不接受任意主机路径。

### 3. 事件与结果使用同一时间线

Run 事件继续由现有事件发布/持久化机制产生；前端按事件序号或时间戳增量合并，重复事件幂等处理。Run 进入终态后追加一个最终结果卡片，来源为 `serialize_code_result`，不由前端推断成功。

### 4. 断线恢复优先读取服务端事实

打开会话或刷新页面时，先读取当前 Run 的历史事件和结果，再订阅后续事件。若 Workspace 已被清理，显示保留期结束或不可用状态，不回退到 `/workplace`。

## Risks / Trade-offs

- [Workspace 目录可能在结果展示前被清理] → 返回明确的 retained/expired 状态，并保留最终结果和 Patch 下载能力。
- [事件数量过大影响对话性能] → 服务端分页/最近窗口，前端折叠低优先级工具细节。
- [Skill 内容包含旧 `/workplace` 路径] → CodeAgent 注入时统一转换为 `/workspace`，并在运行事实中记录实际工作目录。
- [同时存在通用 Agent 与 CodeAgent UI 语义] → 仅在 Code Profile 且存在 Code Run 时启用 Code Workspace，其余保持现有 `/workplace`。

## Migration Plan

先发布后端只读 Workspace/事件接口，再发布前端切换逻辑；旧 Agent 不受影响。若出现权限或路径问题，可关闭 Code Workspace 入口，回退到现有 CodeAgent 结果页，不改变已有 Run 数据。

## Open Questions

无。接口字段和保留策略可在 tasks 阶段按现有模型落地，不改变本设计边界。
