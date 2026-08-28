# Findings — react-engine-v10（执行中记录）

记录执行过程中发现的问题、张力与决策。与 `progress.md` 互补：本文件记录「问题/决策」，`progress.md` 记录「完成事实」。

## F1 — 去重键的实现位置（task 2.1 / 2.3）

tasks.md 2.1 写「`agent_tools.py` READ/SEARCH 返回结果带去重键标识」，2.3 写「`context_manager.py` 接收/回显去重命中指针」。

实际落地：`agent_tools.py` 新增 `dedup_descriptor(action, reply, sandbox)` 纯函数，按 `action` + 协议回文导出 `{'key', 'rel', 'mtime'}`，**未改动 `execute_action` 的 `str` 返回契约**（保持既有语义不变）。runtime 侧在工具执行前用 `dedup_descriptor` 组键查 `state.query_cache`，命中即回显指针、跳过 `execute_action`。

`context_manager.py` **无需改动**：命中指针是短文本，`push_tool_result` 已可透传，直接作为 tool 结果进入上下文，与 MCP `_cached_reference` 的通道一致。此为对 2.3 的「无需新代码」落点，特此记录。

## F2 — 「3 次 / 5 次」语义与退避时长（task 5.2）

spec 写「529/429 用 3 次、5xx 用 5 次」；tasks.md 5.1 写「复用 `_post_with_transport_retry` 骨架」、5.2 写「约 2s→6s→18s」。

实际落地：`_retryable_status_attempts` 返回的是**总尝试次数**（transport 骨架默认 `attempts=5`，retryable HTTP 用固定预算 3 / 5），与「复用 2/4/8/16 骨架」一致。spec 正文「约 2s→6s→18s」是近似描述——实际退避为 `2*(2**attempt)` + jitter = 2s / 4s / 8s / 16s（复用既有指数退避），非 2/6/18。已按「总次数 + 2/4/8/16 骨架」落地，符合 R5 的 resilience 意图。

## F3 — `query_cache` 无界增长（design Open Question，延后）

R2 扩展 `query_cache` 从 MCP-only 到 READ/SHELL/SEARCH，缓存条目随会话无界累积。design.md 已将此列为 Open Question（是否加 LRU / 上限）。本次**按既有 MCP 缓存形态原样扩展**（同 `query_cache` 容器、同 `{path, tool, size, mtime}` 形状），不新增淘汰策略——避免超出 spec 范围、引入新的静态阈值（违背铁律）。留待后续 change 统一处理。

## F4 — `_reflect_final` 对 `ctx.sandbox` 的防御式访问（task 4.1）

R4 让 `_reflect_final` 读 `ctx.sandbox` 拼交付物摘要。为不破坏既有 `test_final_reflection.py`（其 `ctx` 未含 `sandbox` 字段），落点用 `getattr(ctx, "sandbox", None)`，与同函数内 `getattr(ctx.agent, "memory", "")` 的防御式风格一致。`_deliverable_evidence` 内部亦对 `sandbox=None` 兜底为 `"default"`。
