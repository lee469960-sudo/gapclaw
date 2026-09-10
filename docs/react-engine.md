# Agent 引擎设计说明（单引擎 ReAct · v1）

面向开发者的引擎内部地图。Agent 侧操作手册见 Skill SOP。

本文描述**平台如何编排**（单 LLM 循环 / 安全拦截 / 软提示 / 断点续跑 / 上下文隔离 / 实时推送）；SOP 描述**模型应如何写 PLAN / MCP / FINAL**。两者分工，勿混为一谈。

> 阈值、别名等易变数值一律以代码为准；本文标注 `(以代码为准)` 处不代表任何新约定。

---

## 1. 定位与模块地图

生产聊天走 [`agent_chat`](../apps/api/app/routers/agent_chat.py) → [`run_agent`](../apps/api/app/services/agent_runtime/runtime.py) →
[`AgentRuntime.run`](../apps/api/app/services/agent_runtime/runtime.py)。

`run_agent` 是唯一入口，五个调用方：
- `routers/agent_chat.py` —— Web 聊天
- `routers/websockets.py`（`external_agent_api`）—— 外部 CGI
- `services/channels/runtime.py`（`process_inbound`）—— IM 渠道入站
- `services/tick_scheduler.py` —— 定时任务
- `services/workflow_runner.py` —— 工作流

```text
run_agent
  ├─ 解析配置（LLM / 沙箱 / allowed_actions / skills / mcps / rags / note）
  ├─ 构建 AgentContext（不可变快照）
  └─ AgentRuntime.run(ctx)
       ├─ 纯对话（无 MCP/Skill/RAG/HttpMCP）→ ConversationalHandler 单轮
       └─ 其余 → AgentRuntime._run_modular（单 LLM 驱动 ReAct 循环）
```

### 1.1 核心原则

**模型自主决策，引擎只做协议解析 + 执行 + 安全拦截 + 软提示 + 少量硬停。**

- 完成与否、是否继续、是否换工具，全部由主循环里的 LLM 自己判断。
- 引擎**不做**相位迁移、规则验证、完成度判定、软拒绝。
- 硬边界收窄到两类：**安全门禁**（`tool ∈ allowed_actions`）与**进程级硬停**（用户取消 / LLM 连续故障 / 预算耗尽，见 §5）。
- 其余信号（连续纯文本、重复、同工具失败、FINAL 同轮工具、子任务提醒等）一律走**软提示**（coach hint）。

### 1.2 模块表

| 模块 | 职责 |
|------|------|
| [`runtime.py`](../apps/api/app/services/agent_runtime/runtime.py) | `run_agent` / `AgentRuntime.run`；单循环编排、步骤可见性、消息落盘、安全门禁、chat 隔离、入站事件、checkpoint 续跑、`_reflect_final`/`_distill_final`/`_rescue_leaked_code` |
| [`context.py`](../apps/api/app/services/agent_runtime/context.py) | `AgentContext`：不可变调用快照（`frozen=True`，`from_params` 一次解析） |
| [`loop_state.py`](../apps/api/app/services/agent_runtime/loop_state.py) | `AgentLoopState`：每轮可变状态（final / run_steps / saved_paths / progress_lines / subtasks / goal / run_ts / mcp_results / query_cache / plan_text） |
| [`context_manager.py`](../apps/api/app/services/agent_runtime/context_manager.py) | 分层上下文 + 归档；coach_hint 聚合（`add_coach_hint`/`flush_coach_hints`） |
| [`system_prompt.py`](../apps/api/app/services/agent_runtime/system_prompt.py) | 身份 / 工具目录（中性，无业务 SOP）/ skill 快照 / 目录清单 / 各 coach hint / 原生 function schema |
| [`conversational.py`](../apps/api/app/services/agent_runtime/conversational.py) | 无工具纯对话路径 |
| [`hub.py`](../apps/api/app/services/agent_runtime/hub.py) | WS pub/sub + `_running` / `stop_chat` / `is_running` / `inbox_key` |
| [`utils.py`](../apps/api/app/services/agent_runtime/utils.py) | MCP 工具元数据缓存（`_get_mcp_tools_cached`，600s TTL）+ 中性目录格式化（`_format_mcp_tools_for_prompt`） |
| [`tool_parser.py`](../apps/api/app/services/tool_parser.py) | 协议行解析：工具步骤抽取、FINAL 识别、展示清理、`parse_subtasks`、`_strip_reasoning_blocks` |
| [`agent_tools.py`](../apps/api/app/services/agent_tools.py) | `execute_action`：按 action 分发执行 + 安全拦截（路径/二进制/热重载）；MCP 分支按工具名多 MCP 分派 |
| [`mcp_client.py`](../apps/api/app/services/mcp_client.py) | `call_mcp_tool`（纯 transport，供测试端点）+ `McpSessionManager`（会话复用 + 失效恢复 + 查询去重/落盘） |
| [`session_summary.py`](../apps/api/app/services/session_summary.py) | 会话滚动总结（按 chat_id 隔离） |
| [`skill_loader.py`](../apps/api/app/services/skill_loader.py) | `load_skill_mds`（skill markdown + 引用片段） |
| [`channels/runtime.py`](../apps/api/app/services/channels/runtime.py) | IM 入站编排：去重/限流/会话映射/调用 run_agent/回发 |

> `decision_engine.py` / `tool_executor.py` / `tool_router.py` / `intent_types.py` / `mcp_resource_bind.py` 已在 consolidation 中删除，其解析职责并入 `tool_parser.py`。

### 1.3 模型路由的受控启用与回滚

默认 Agent 继续使用其直接绑定的 `llm_id`。只有 Standard/React Agent 显式绑定有效的“模型路由策略”后，运行开始前才会从该策略允许的角色模型组中选择并冻结一个叶子模型；Code Profile 不支持该绑定。

受控上线按以下顺序操作：先为叶子模型声明能力，再建立角色模型组和策略；记录目标 Agent 当前的直接 LLM；仅为小范围 Standard Agent 绑定策略；在会话“执行过程”中展开“模型路由”检查冻结模型、候选过滤、回退原因和耗时。不要把既有 `type=group` LLM Group 转换为角色模型组。

回滚不需要删策略或审计记录：在 Agent 设置中清空“模型路由策略”并保存。此操作只解除 `routing_policy_id`，不会修改原有 `llm_id` 或 legacy LLM Group 成员；下一次任务立即恢复该 Agent 的直接模型执行。解绑后应发起一次小任务，确认没有新的“模型路由”步骤且实际使用原直接 LLM。

---

## 2. 请求生命周期

```text
run_agent(db, agent, session_id, msg, username, message_meta)
  └─ 解析配置 → AgentContext.from_params(...)
  └─ AgentRuntime.run(ctx)
       ├─ _running[key] = True
       ├─ _save_user_message             # 立即落库，刷新中途仍可见
       ├─ _publish_inbound_events        # 仅 IM 入站 → user_message / inbound 事件
       ├─ 分支:
       │   ├─ _is_conversational → _run_conversational   # 单轮，无协议
       │   └─ _run_modular                                 # ReAct 主循环
       ├─ _save_assistant_message         # 落库 + meta.steps
       └─ _publish_modular_done           # done 事件
  └─ generate_session_summary(chat_id=_context_chat_id(ctx))
```

消息落盘字段：`ChatMessage` 的 `meta` 存 `source` / `chat_id` / `sender_*`（IM 入站时），供上下文隔离与回发使用。

---

## 3. 单循环每轮（`_run_modular`）

```text
async with McpSessionManager(...) as mcp_sessions:   # 会话复用，离开自动 close
  for iteration in range(max_iters):
    0. 取消检查（_running[key]）+ 预算临近 hint + flush_coach_hints()
    1. 调 LLM（system 分层上下文 + 已聚合 coach 软提示 + 历史 + 可选 tools schema）
    2. 解析回复 → ToolStep[]（协议解析，非门禁）
    3. PLAN:      → parse_subtasks → _apply_plan（回显 task_context + 长任务 checkpoint）
    4. FINAL:     → 同轮非 PLAN 工具标记 skipped 告警 → _reflect_final 复核 → return
    5. 无工具步   → _rescue_leaked_code 自动救援裸代码，否则软提示（text_only_streak）
    6. 安全门禁   → action ∈ allowed_actions，否则阻止 + 注入提示
    7. agent_tools.execute_action(action, ..., mcp_sessions=) 执行
    8. 观察结果回灌 ContextManager + 归档（不做规则校验）
    9. 失败/重复   → 软提示（tool_fail_streak / same_sig_run，无硬停）
   10. file_write → 记录 saved_paths / progress_lines
   11. MCP 超大结果 → McpSessionManager 落盘 task/<run_ts>/mcp_result_N.json + 去重记账
   12. 每 8 轮 trim_tool_results
```

预算耗尽且无 FINAL → `_distill_final` 兜底（见 §5）。

### 3.1 原生 function-calling（可选）

对 OpenAI 兼容 provider（`openai`/`minimax`/`deepseek`）声明元工具集（`build_tool_schemas`：shell/file_write/file_read/file_search_replace/mcp_tool_call/rag_query/skill_read_md/skill_run_script/recall/done），模型以 `tool_calls` 返回；`llm_client.extract_chat_response_text` 将其归一化为文本协议行，后续解析/执行路径与文本协议完全一致。其余 provider 走文本协议回退。

---

## 4. 上下文分层（ContextManager）

消息数组按「层」管理，每层有已知位置区间，替换为 O(1)，不扫描数组：

```
1. base          系统提示 + 长期记忆 + 滚动总结    (不可变，折叠为一条)
2. task_context  任务目标 + 当前 PLAN              (每轮回显)
3. tools_catalog 可用工具目录
4. progress_block 本轮进度                         (upsert)
5. coach_hint    动态教练提示                       (每轮替换)
6. history       user/assistant 历史               (append)
7. tool_result   工具执行结果                       (append + 定期裁剪)
── 归档 archive  工具结果全量保存(不进推理上下文) → recall() 关键词检索
```

### 4.1 软提示聚合（coach_hint 多来源不覆盖）

`coach_hint` 是单槽 replace 语义。引擎多类提示（FINAL 同轮工具、卡死、失败/重复、子任务提醒、text-only、预算临近）可能在同一迭代并发产生，为避免互相覆盖：

- 环内各点调用 `add_coach_hint(hint)` 累积到 `_coach_hint_buffer`（去重 + strip）。
- 每轮环顶调用 `flush_coach_hints()`：把本迭代收集的多条 hint 带短标签 join 成一条，再 `_replace_layer` 一次。
- 建层仍用 `push_coach_hint`（setup 阶段建立单槽）；环内一律 `add_coach_hint` + `flush_coach_hints`。

---

## 5. 结束与兜底（FINAL 优先 + 少量硬停）

### 5.1 FINAL 语义

- FINAL 优先契约不变：同轮同时输出 FINAL 与非 PLAN 工具时，FINAL 生效，其余工具**不执行**。
- 引擎在 FINAL 分支内计算 `skipped = [非 PLAN 非 FINAL 工具步骤]`，非空时注入一条可见步骤（`final_skipped_tools`）+ 一条软提示（「本轮 FINAL 与工具同时出现，工具未执行」）。不改 FINAL 优先语义，不加硬门禁。
- FINAL 识别（`tool_parser._FINAL_MARKER`）：仅 `FINAL:` 与 `Final Answer:`（行首 + 冒号 + 非空 payload，防误判）。中文别名（最终答案/答案/Answer 等）已移除。

### 5.2 FINAL 复核（`_reflect_final`）

FINAL 候选提交前，用 agent 自己的 LLM 作纯裁判（无工具）复核是否完成实质要求：

- 输入：任务目标 + 长期规则 + 已保存文件 + **渲染后子任务清单 + 最近 progress** + 候选回复。
- 输出 `PASS` → 直接采用并结束；输出 `FAIL` → 返回 `{missing, fix_list, revised_plan}`，重新应用修订 PLAN + 注入「完成度反思」软提示后**继续循环**（非硬门禁）。
- 复核不可用（LLM 异常）→ 不阻断 FINAL，原样采用。

### 5.3 预算耗尽兜底（`_distill_final`）

循环耗尽 `max_iters` 且无 FINAL 时，一次性总结已取得进展 / 交付文件 / 未完成子任务，产出诚实的最佳收尾（明确标注缺口）。LLM 异常时回退 `_forced_stop_reply`（报原因 + 已完成进度 + 交付文件）。

### 5.4 硬停边界（仅三类）

| 硬停 | 条件 | 行为 |
|------|------|------|
| 用户取消 | `_running[key]` 变 False | `_clear_run_state` + 返回「已停止」 |
| LLM 连续故障 | `llm_failures >= 2` | 返回暂停说明，保留执行记录供续跑 |
| 预算耗尽 | 达到 `max_iters` 且无 FINAL | `_distill_final` 兜底 |

其余（连续纯文本、重复回复、同工具重复成功/失败、`soft_circuit`）均为**软提示**——`soft_circuit` 等数值只作为提示文案里的参考值，不触发硬停。安全门禁（`tool ∉ allowed_actions`）只阻止该次执行 + 注入提示，不结束循环。

### 5.5 LLM 传输层 resilience（退避重试 + 组内降级）

`llm_client.chat_completion` 内层 `_post_with_transport_retry` 对两类瞬态失败做指数退避（2/4/8/16s + jitter）：

- 传输错误（`httpx.TransportError`）：最多 5 次。
- 可重试 HTTP 状态码：**529（过载）/ 429（限流）→ 3 次；502/503/504（瞬态 5xx）→ 5 次**。
- 确定性失败（400/401/2013/1026/1027）不重试，直接走 `format_llm_http_error` 文案（含 529/429/5xx 专属提示）。

**组内降级（group failover）**：Agent 绑定 `type:"group"` 的 LLM 时，`chat_completion` 依次尝试组内成员，某成员抛异常即切下一个，全部失败才抛错——即「绑 group 获得多模型降级」。该机制不在 V10 改动范围内（仅文档说明，不新增健康度 / 轮换逻辑）。

---

## 6. 续跑层（checkpoint 扩展 + 查询去重）

### 6.1 checkpoint 扩展

`_save_run_state` / `_load_run_state` / `_clear_run_state` 针对 `(agent, session)` upsert 到 `AgentRunState`。payload：

```text
goal / subtasks([{text,status}]) / progress_lines / saved_paths / files_written / last_reply
+ run_ts / mcp_results([{seq,path,tool,args,size}]) / query_cache({key:{path,tool,size}}) / plan_text
```

- `run_ts` 首轮生成（`str(int(time.time()*1000))`）、落 checkpoint；续跑复用同一 `task/<ts>/`，不再孤立旧产物。
- `mcp_result_seq` 续跑由 `len(mcp_results)` 恢复（去重落盘时 `seq = len(mcp_results)`）。
- 旧 checkpoint 缺新字段按空处理（向后兼容）。
- 长任务 = 子任务 ≥ 2；`_apply_plan` 在长任务时写 checkpoint；短任务（无子任务）不写。

### 6.2 续跑上下文注入

续跑（`state.resumed`）时：

1. `cm.set_task_context(goal, plan=_render_subtask_list(subtasks) + "\n\n" + plan_text)` —— 让 `[x]`/`[ ]` 与原始 plan_text（承载 view_map 等）真正进入上下文。
2. 回填 `mcp_results` 的 path 与 `saved_paths` 进 progress_block（模型据此跳过已确认产物、从首个 pending 续）。

### 6.3 查询去重（两层生命周期）

执行 `mcp_tool_call` 前算 `key = f"{mcp_id}\x00{tool}\x00{规范化 args}"`（排序 JSON）：

- 命中 → 返回「已缓存，结果见 path」，不重调、不落新文件、不重复 push tool_result。
- 未命中 → 照常调用；超大结果落盘 `task/<run_ts>/mcp_result_N.json`，记入 `mcp_results` + `query_cache`。
- 两层生命周期：in-run（内存，去重主收益）+ cross-run（checkpoint 持久化，供长任务续跑避免重查）。
- 缓存上限（仅内存卫生，非硬门禁）：`max_cache_entries`（FIFO 丢最旧）+ `max_cached_result_chars`（单条 size 上限），防 `AgentRunState.state` blob 膨胀。

**READ/SHELL/SEARCH 去重（V10 R2/R3）**：同一 `query_cache` 容器按 `动作 + 目标` 扩展覆盖 READ（路径）、SHELL（规范化命令）、SEARCH（query）；命中回显「该结果已缓存，请引用之前结果」指针，不重读/重跑。READ 条目额外记 `mtime`，`file_write` 命中同一路径即失效该 READ 缓存，避免改写后回旧缓存。

---

## 7. MCP 执行层（McpSessionManager）

`McpSessionManager` 由 `_run_modular` `async with` 包裹，按 `mcp.id` 缓存会话：

- **复用**：stdio 子进程 + streamable HTTP 会话在单次运行内 `initialize` 一次、多次 `tools/call`；legacy `http`/`rest` 无状态、直调不缓存。
- **失效恢复**：stdio 子进程退出 / streamable 连接或 session 错误 → 丢旧会话重建一次 + 该次调用重试一次（分层于 `call_mcp_tool` 的 transport 重试之上）。
- **关闭**：`close()` 逐会话吞掉关闭异常，不遮蔽主循环原始异常；`async with` 保证取消/异常等提前返回路径也触发 close。
- **可观测**：`_observe()` 记录 create / reuse / rebuild / materialize / dedup_hit 五类事件（写 `events` + `logger.info`）。

`call_mcp_tool` 保持纯 transport 语义不变（供 MCP 测试端点一次性调用）；目录构建仍走 `_get_mcp_tools_cached`（600s 跨 run 缓存），与执行期会话复用不合并。

### 7.1 多 MCP 分派

`execute_action` 的 MCP 分支遍历绑定 MCP，用工具目录（`_get_mcp_tools_cached`）按工具名匹配实际声明该工具的 MCP；命中才执行，无命中返回「未在绑定 MCP 中找到工具 X」。单 MCP 绑定行为不变。

---

## 8. 上下文隔离（按 chat）

IM 渠道把所有外部会话（不同 TG 用户/群）映射到**同一 web 会话**（`ensure_im_web_session`，如「Telegram」）。为避免不同 chat 的历史/总结互相污染：

- `_context_chat_id(ctx)`：`message_meta.source` 以 `im:` 开头时取 `chat_id`，否则 `""`（web/legacy 归空桶）。
- `_load_recent_history`：按 `chat_id` 过滤（宽窗 `max_msgs*4+1` 防饿死，过滤后再截 `max_msgs`）。
- `ChatSummary.chat_id` 列 + 滚动总结按 chat 隔离（`session_summary` 全链路带 `chat_id` 形参）。
- `startup.py` 对已有库做幂等 `ALTER TABLE chat_summaries ADD COLUMN chat_id`。

共享 web 视图不变（`get_history` 仍能看到该 provider 全部消息），仅 LLM 注入上下文按 chat 隔离。

---

## 9. 实时推送（hub + 入站事件）

`ChatStreamHub`（`hub.py`）是进程内 pub/sub；发布者在后台线程的 `asyncio.run` 循环，订阅者在 WebSocket 循环，`publish` 用 `run_coroutine_threadsafe` 跨循环安全投递。

- 订阅 key：会话级 `{agent_id}:{session_id}` + agent 级 `agent_inbox:{agent_id}`（`inbox_key`）。
- 事件类型：
  - `step`（op=append/patch）—— 执行步骤流式可见
  - `done` —— 最终结果
  - `user_message` —— IM 入站即时气泡（仅 `im:*` 源）
  - `inbound` —— 跨会话未读角标（agent 级广播）

`_publish_inbound_events`（`runtime.py`）在 `_save_user_message` 后、仅当 `source.startswith("im:")` 时向会话 key 发 `user_message`、向 inbox key 发 `inbound`。Web 自提交靠本地乐观追加 + step/done，不重复推送。

---

## 10. 执行 / 安全层（agent_tools.execute_action）

按 action 分发，含大量拦截：

- `shell` → 拦截 `ls /workplace` / `find` / `mkdir workplace` / `task/*.py` 写入（防 API 热重载）→ 缺依赖一键装提示 → `docker exec`（`asyncio.to_thread`）。
- `file_read` / `file_write` / `file_search_replace` → 路径安全校验 + 二进制写阻断（xlsx/pdf 必须走 SHELL+pandas）。
- `mcp_tool_call` → 按工具名多 MCP 分派（§7.1），经 `mcp_sessions.call_tool`（复用/去重/失效恢复）。
- `httpmcp_call` / `rag_query` / `skill_*` / `recall` → 各自客户端。

敏感字段脱敏、禁止默认全量拉取等软护栏由 `system_prompt` 的 coach hint 承担（非硬拦截）。

---

## 11. 渠道层（channels/runtime.py）

```text
webhook / poller → process_inbound
  → try_dedup → _rate_ok → _running_keys 防重入
  → get_or_create_im_session（映射到共享 web_sid，如「Telegram」）
  → adapter.send_text("已收到，正在处理…")
  → run_agent(message_meta={source, channel_id, chat_id, sender_*})
  → format_im_completion_reply + _push_saved_documents（回推 xlsx 附件）
```

---

## 12. 维护提示

| 要改什么 | 改哪里 |
|----------|--------|
| 入口 / 单循环 / 硬停 / 步骤可见性 / chat 隔离 / 续跑 | `agent_runtime/runtime.py` |
| 协议解析 / FINAL 识别 / 展示清理 / 子任务解析 | `services/tool_parser.py` |
| 工具目录 / 提示文案 / 原生 function schema | `agent_runtime/system_prompt.py` + `agent_runtime/utils.py` |
| 上下文分层 / 裁剪 / 归档 / 软提示聚合 | `agent_runtime/context_manager.py` |
| 工具执行 / 安全拦截 / 多 MCP 分派 | `services/agent_tools.py` |
| MCP 会话复用 / 失效恢复 / 查询去重 / 落盘 | `services/mcp_client.py` |
| WS 事件 / 订阅 key | `agent_runtime/hub.py` + `routers/websockets.py` |
| IM 入站 / 会话映射 / 回发 | `services/channels/runtime.py` |
| 会话滚动总结 | `services/session_summary.py` |
| 业务口径 / 导出 SOP 文案 | Skill `references/*.md`（**不要**写进引擎） |
