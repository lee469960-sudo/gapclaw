# Task Plan: react-engine-v4（OpenSpec 执行计划）

<!--
  本文件是 planning-with-files 的执行计划，把 OpenSpec tasks.md 映射为执行 phases。
  ⚠️ 正式任务来源 = openspec/changes/react-engine-v4/tasks.md（唯一，勾选以它为准）。
  ⚠️ 验收标准来源 = openspec/changes/react-engine-v4/specs/agent-runtime/spec.md（WHEN/THEN/AND）。
  ⚠️ 需求来源 = docs/exploration/react-engine-v4.md。
  本文件不重新定义需求/任务，只做 phase 分组 + 状态跟踪 + 决策/错误记录。
  每完成一个 OpenSpec task：先更新 progress.md → 确认代码+测试完成 → 再勾选 tasks.md。
-->

## Goal

实施 OpenSpec change `react-engine-v4`（R1 截断可配置、R2 令牌剥离、R3 MCP 提速、R4 步骤可见、R5 role:tool 原生回填），全部满足 specs 验收标准、全量测试绿、无新增静态门禁。

## Next Step

执行 task 1.1：`models.py` 新增 `tool_result_clip` 列 + `to_dict`。

## Current Phase

Phase 1

## Phases

### Phase 1: R1 截断可配置 + 更大输出（tasks 1.1–1.5）
- 1.1 `models.py` 列（Integer, default 6000）+ `to_dict`
- 1.2 `routers/agent.py` `AgentBody` 声明 + 写逻辑 clamp `max(1, min(.., 100000))`
- 1.3 `startup.py` 幂等 `ALTER TABLE agents ADD COLUMN tool_result_clip`
- 1.4 `Agents.vue` 表单暴露
- 1.5 `runtime.py` `max_tokens=8192` + `tool_result_clip` 接线（push_tool_result + MCP 落盘阈值）
- **Status:** in_progress

### Phase 2: R2 令牌泄漏剥离（tasks 2.1–2.4）
- 2.1 `tool_parser.py` `LEAKED_TOOL_TOKEN_RE` 覆盖 `<|...|>` 与 `]<...>[`
- 2.2 `tool_parser.py` `_norm_path` 剥离首尾方括号
- 2.3 `tool_parser.py` `extract_tool_steps` 解析前剥离
- 2.4 `llm_client.py` `extract_chat_response_text` 归一化时剥离
- **Status:** pending

### Phase 3: R3 MCP 提速（tasks 3.1–3.2）
- 3.1 `utils.py` 工具目录 `tools/list` 跨 run 缓存（600s TTL）
- 3.2 `system_prompt.py` 绑定 MCP 时追加「取数效率」引导（中性）
- **Status:** pending

### Phase 4: R4 步骤可见性（tasks 4.1–4.2）
- 4.1 `runtime.py` `_tool_step_title` 输出真实命令 + 参数预览
- 4.2 `runtime.py` 成功步骤也保留 content（截断）
- **Status:** pending

### Phase 5: R5 role:tool 原生回填（tasks 5.1–5.6）
- 5.1 `tool_parser.py` `ToolStep` 加 `tool_call_id`
- 5.2 `llm_client.py` 原生 `tool_calls` 直接生成带 id 的 `ToolStep`（单一来源）
- 5.3 `llm_client.py` `normalize_chat_messages` 透传 `tool_calls` / `tool_call_id`
- 5.4 `llm_client.py` `fit_messages_to_context` 成组裁剪（原子组）
- 5.5 `context_manager.py` `push_assistant_native` + role:tool 结果推送
- 5.6 `runtime.py` 循环按「响应是否含 tool_calls」分流回填形态
- **Status:** pending

### Phase 6: 测试与回归（tasks 6.1–6.5）
- 6.1 `tool_result_clip` 配置接线 + 路由 clamp 单测
- 6.2 泄漏令牌剥离单测（两种令牌 + `xxx.py]` 归一化）
- 6.3 `ToolStep.tool_call_id` + 原生步骤来源单测
- 6.4 归一化透传 + 成组裁剪单测（不孤立 tool 消息）
- 6.5 全量测试回归绿；无新增静态门禁 / 硬中断
- **Status:** pending

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| （执行中产生的新决策记录于此；设计决策见 design.md D1–D9） | |

## Errors Encountered

| Error | Resolution |
|-------|------------|
| （执行中产生） | |
