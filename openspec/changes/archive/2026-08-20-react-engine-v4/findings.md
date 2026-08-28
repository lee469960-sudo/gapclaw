# Findings — react-engine-v4（执行中记录）

记录执行过程中发现的问题、张力与决策。与 `progress.md` 互补：本文件记录「问题/决策」，`progress.md` 记录「完成事实」。

## F1（设计张力，已解决）小结果内联 vs 全量落盘

- **问题**：任务 1.6「去 `>6000` 门禁、所有非空结果落盘」若直接实现为「所有结果都返回 `_materialize` 的『已写入 path，请 READ』消息」，会让小结果也丢失上下文内联内容，模型每轮多一次 READ 往返，违背 D10「`tool_result_clip` 截断不变，只影响展示」。
- **决策**：`call_tool` 对**所有**非空结果执行 `_materialize` 作为持久化副作用（写盘 + 入 cache）；但**返回值**仍分流——大结果（> `large_result_chars`）回传「已写入 path」引用，小结果回传原文内联。
- **依据**：spec「MCP 结果全量落盘」场景只要求「落盘 + 入 cache」，不要求改变小结果返回值；D10 明确「上下文的 `tool_result_clip` 截断不变（只影响展示）」。

## F2（行为变更，需更新既有单测）去重扩展到小结果

- **问题**：去重门禁去掉后，`test_mcp_session_manager.py` 的 `test_stdio_session_reused_across_calls`（两次调用同参 `{}`）会第二次命中 cache、返回「已缓存」而非 `ok:tools/call`，`tool_calls == 2` 断言被破坏。
- **处理**：该测试意图是「会话复用」，不是「去重」。改用不同参数（`{}` vs `{"page": 1}`）保留复用语义并避开去重；同时为涉及 `_materialize` 的调用 patch `workplace_root` 到 `tmp_path`，避免测试期写入真实 workplace。
- **依据**：spec R6「相同签名再次出现时 MUST 命中 cache」——同参调用被去重是**预期行为**，不是回归。

## F3（确认项，无需改码）`_dedup_key` 已满足规范化

- **确认**：`_dedup_key = f"{mid}\x00{tool}\x00{json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)}"` 已满足「键保留、值参与、忽略空白/键序」：`sort_keys=True` 忽略键序、`json.dumps` 归一空白、值参与签名。任务 6.1 为验证型任务，仅补单测覆盖（8.6）。

## F4（确认项）`_cached_reference` 已回传 path

- **确认**：`_cached_reference` 已返回 `结果见 {path}`；任务 6.2 主体已完成，1.7 仅补 shape（元素个数/行数）进该消息。

## F5（范围界定）`max_cached_result_chars`（2M）保留

- **决策**：任务 1.6 去的是 `large_result_chars`（6000）门禁；`max_cached_result_chars=2_000_000` 是跨 run `query_cache` 的内存卫生上限（与 `max_cache_entries` 同类），非「截断阈值」，予以保留，避免无界缓存增长。
