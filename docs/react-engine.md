# Agent 引擎设计说明（单引擎）

面向开发者的引擎内部地图。Agent 侧操作手册见 Skill SOP。

本文描述**平台如何编排**（单 LLM 循环 / 安全拦截 / 软提示）；SOP 描述**模型应如何写
PLAN / MCP / FINAL**。两者分工，勿混为一谈。

---

## 1. 定位与模块地图

生产聊天走 [`agent_chat`](../apps/api/app/routers/agent_chat.py) → [`run_agent`](../apps/api/app/services/agent_runtime/runtime.py) →
[`AgentRuntime.run`](../apps/api/app/services/agent_runtime/runtime.py)。

```text
agent_chat → run_agent
  → ConversationalHandler（无 MCP / Skill / RAG / HttpMCP 的纯对话）
  → AgentRuntime._run_modular（单 LLM 驱动 ReAct 循环）
```

### 1.1 核心原则

**模型自主决策，引擎只做协议解析 + 执行 + 安全拦截 + 软提示。**

- 完成与否、是否继续、是否换工具，全部由主循环里的 LLM 自己判断。
- 引擎**不再**做相位迁移、预算钳制、规则验证、完成度判定、软拒绝。
- 唯一保留的硬边界是**安全门禁**：`tool ∈ allowed_actions`，否则阻止执行并注入提示。

### 1.2 模块表

| 模块 | 职责 |
|------|------|
| [`runtime.py`](../apps/api/app/services/agent_runtime/runtime.py) | `run_agent` / `AgentRuntime.run`；单循环编排、步骤可见性、消息落盘 |
| [`context.py`](../apps/api/app/services/agent_runtime/context.py) | `AgentContext`：不可变调用快照 |
| [`hub.py`](../apps/api/app/services/agent_runtime/hub.py) | WS pub/sub + `_running` / `stop_chat` / `is_running` |
| [`context_manager.py`](../apps/api/app/services/agent_runtime/context_manager.py) | 分层上下文（base / task / skill / tools / progress / coach / history / tool_result） |
| [`message_manager.py`](../apps/api/app/services/agent_runtime/message_manager.py) | 工具结果裁剪、进度块、路径抽取 |
| [`system_prompt.py`](../apps/api/app/services/agent_runtime/system_prompt.py) | 身份 / 工具目录 / skill 快照 / 目录清单等提示构建 |
| [`tool_router.py`](../apps/api/app/services/agent_runtime/tool_router.py) | 按绑定与权限构建工具目录 |
| [`tool_executor.py`](../apps/api/app/services/agent_runtime/tool_executor.py) | `execute` 委托 `agent_tools`；沙箱启动 |
| [`decision_engine.py`](../apps/api/app/services/agent_runtime/decision_engine.py) | **纯解析器**：协议行检测、工具步骤抽取、展示清理（无决策/门禁） |
| [`conversational.py`](../apps/api/app/services/agent_runtime/conversational.py) | 无工具纯对话路径 |
| [`loop_state.py`](../apps/api/app/services/agent_runtime/loop_state.py) | 每轮可变状态（final / run_steps / saved_paths / progress 等） |
| [`utils.py`](../apps/api/app/services/agent_runtime/utils.py) | MCP 工具元数据缓存 + 裁剪常量 |
| [`session_summary.py`](../apps/api/app/services/session_summary.py) | 会话滚动总结 |
| [`skill_loader.py`](../apps/api/app/services/skill_loader.py) | `load_skill_mds`（skill markdown + 引用片段） |
| [`intent_types.py`](../apps/api/app/services/intent_types.py) | 资源绑定护栏用的指标意图类型 |
| [`mcp_resource_bind.py`](../apps/api/app/services/mcp_resource_bind.py) | 「禁止默认全量拉取」白名单（安全/成本护栏，软限制） |

### 1.3 单循环每轮

```text
1. 调 LLM（system + 工具目录 + coach 软提示 + 历史）
2. 解析回复：工具调用 或 FINAL（协议解析，非门禁）
3. 安全门禁：action ∈ allowed_actions，否则阻止 + 注入提示
4. ToolExecutor.execute 执行
5. 观察结果回灌 ContextManager（不做规则校验）
6. 由 LLM 决定：继续 / 换工具 / 完成（无强制相位/预算/验证门禁）
7. FINAL → 清理展示并返回
```

---

## 2. 保留的安全边界

| 边界 | 位置 | 说明 |
|------|------|------|
| 权限门禁 | `_run_modular` 循环内 | 工具不在 `allowed_actions` 则阻止执行 |
| 沙箱安全 | `tool_executor` / `agent_tools` | shell 执行隔离、路径约束 |
| 凭据处理 | `tool_parser` / trace recorder | 敏感字段脱敏 |
| 全量拉取护栏 | `mcp_resource_bind` | 禁止默认全量拉取的软白名单提示 |

---

## 3. 软提示（soft coach，非门禁）

- 连续多轮只输出文字不执行工具 → 注入「请选择工具或 FINAL」提示。
- 绑定了 MCP/Skill 但未开启对应权限 → 注入配置警告。
- 工作目录清单、保存目录规则 → 注入 system 提示。

以上均为**软提示**，不会强制改变 LLM 决策。

---

## 4. 落盘与步骤可见性

- `AgentRuntime._save_assistant_message` 落盘 assistant 回复 + `meta.steps`（含 `step_count`）。
- `_ensure_visible_run_steps` 保证 ≥1 可视执行步骤。
- 工具结果经 `ContextManager.push_tool_result` 回灌，长结果裁剪保留关键行。

---

## 5. 维护提示

| 要改什么 | 改哪里 |
|----------|--------|
| 入口 / 单循环 / 步骤可见性 | `agent_runtime/runtime.py` |
| 协议解析 / 展示清理 | `agent_runtime/decision_engine.py` + `tool_parser.py` |
| 工具目录 / 权限 | `agent_runtime/tool_router.py` + `system_prompt.py` |
| 上下文分层 / 裁剪 | `agent_runtime/context_manager.py` / `message_manager.py` |
| 会话总结 | `services/session_summary.py` |
| 业务口径 / 导出 SOP 文案 | Skill `references/*.md`（**不要**写进引擎） |
