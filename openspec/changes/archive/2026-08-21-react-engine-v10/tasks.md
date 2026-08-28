## 1. R1 — 规划前置验证（agent-runtime）

- [x] 1.1 `runtime.py` `_apply_plan` 加前置本地静态检查（计划动作 ∈ `allowed_actions`；计划空/不可解析）
- [x] 1.2 命中时软 `coach_hint` 提示（不阻断、不额外调 LLM）
- [x] 1.3 确认零 LLM 调用（静态检查在解析函数内完成）

## 2. R2 — 工具结果去重复用（agent-runtime）

- [x] 2.1 `agent_tools.py` READ/SEARCH 返回结果带去重键标识（路径 / query）
- [x] 2.2 `runtime.py` 工具执行前查 `query_cache`，命中回显「已缓存，请引用」指针（对齐 MCP `_cached_reference` 形态）
- [x] 2.3 `context_manager.py` 接收/回显去重命中指针
- [x] 2.4 `system_prompt.py` 去重指针用法说明（引用缓存，不重读/重跑）

## 3. R3 — 去重结果内容失效（agent-runtime）

- [x] 3.1 READ 结果记「路径 + mtime/hash」
- [x] 3.2 `file_write` 命中同一路径 → 使该路径缓存失效

## 4. R4 — 完成度复核交付物证据（agent-runtime）

- [x] 4.1 `_reflect_final` prompt 拼入 `saved_paths` 交付物「前 N 行内容摘要」
- [x] 4.2 保持纯裁判、不调工具、`_REFLECT_FAIL_CONVERGE=3` 不变

## 5. R5 — 可重试 HTTP 状态码退避重试（agent-runtime）

- [x] 5.1 `llm_client.py` 可重试状态码（529/429/502/503/504）接入指数退避重试（复用 `_post_with_transport_retry` 骨架）
- [x] 5.2 529/429 用 3 次、5xx 用 5 次；400/401/2013/1026/1027 不重试
- [x] 5.3 `format_llm_http_error` 补 529/429/5xx 文案
- [x] 5.4 不改 group failover 代码（D6）；文档引导「Agent 绑 group 获得多模型降级」

## 6. 测试与回归

- [x] 6.1 前置验证单测（越权计划 → coach hint；权限内计划 → 无提示；零 LLM 调用）
- [x] 6.2 去重命中/失效单测（重复 READ/SHELL → 指针不重跑；WRITE 后 → 缓存失效重读）
- [x] 6.3 Verifier 交付物证据单测（交付物摘要进入 prompt；FAIL 依据内容而非文本）
- [x] 6.4 退避重试单测（529/429/5xx 有 sleep + 次数上限；400/401/2013/1026/1027 直接报错）
- [x] 6.5 全量回归绿；无新增循环门禁 / 静态阈值
