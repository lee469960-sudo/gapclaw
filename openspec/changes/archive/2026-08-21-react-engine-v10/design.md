# Design — react-engine-v10

## Context

动机与根因见 `proposal.md`「Why」与 `docs/exploration/react-engine-v10.md`。铁律贯穿：

- **铁律**：无任何静态硬门禁。V10 五条需求全部落在「本地静态检查 + 软提示 + 传输层 resilience」上，不引入循环级计数器 / 阈值 / 硬停；循环仍只由 `FINAL` / 用户取消 / LLM 错误 / `max_iters` 结束。
- 循环现状：`_run_modular` 每轮 `chat_completion`，`_apply_plan`（`runtime.py:1412`）解析 PLAN 子任务；`_reflect_final`（`runtime.py:475`）纯裁判在 `FINAL:` 判一次，`_REFLECT_FAIL_CONVERGE=3`（`runtime.py:955`）防死循环。
- 去重现状：`query_cache`（`loop_state.py:28`）由 `McpSessionManager` 维护，只覆盖 MCP；`_is_cached_reference`（`runtime.py:1578`）识别 MCP 缓存命中回显。
- 退避现状：`_post_with_transport_retry`（`llm_client.py:719-752`）只对 `httpx.TransportError` 指数退避 2/4/8/16s；`httpx.HTTPStatusError`（含 529）走 `llm_client.py:768` 直接抛，无退避。

## Goals / Non-Goals

**Goals:**
- 越权/跑偏计划在首轮执行前被软提示拦住（零 LLM 成本）。
- 重复 READ/SHELL/SEARCH 不再重读、重跑、重进上下文。
- Verifier 能据交付物内容判 FAIL，压「误收」。
- 529/429/5xx 有退避重试，不再无等待硬撞即放弃。

**Non-Goals:**
- 不改 `_run_modular` 循环本体、不加循环级门禁。
- 不做额外一次 LLM 的规划校验（本地静态检查）。
- 不改 group failover 代码、不加 group 健康度/轮换。
- 不把 Verifier 变成「带工具」的复核（保持纯裁判）。

## Decisions

### D1: 前置验证零 LLM 成本

`_apply_plan` 解析 PLAN 时同步做本地静态检查：① 计划提到的动作是否都在 `allowed_actions` 内；② 计划是否空 / 能否解析出子任务。命中 → 软 `coach_hint`，不阻断、不额外调 LLM。
- **为何**：前置验证若吃一轮 LLM 就违背「最少轮次」；本地检查零轮次，却能拦住「越权计划首轮失败再重规划」的浪费（v8 httpmcp/file_search 教训）。

### D2: 去重覆盖 READ/SHELL/SEARCH

把 `query_cache` 去重从 MCP-only 扩展到 READ/SHELL/SEARCH，去重键 = 动作 + 目标（READ 用路径、SHELL 用规范化命令、SEARCH 用 query）。命中回显「已缓存，请引用之前结果」指针（对齐 MCP `_cached_reference` 形态）。
- **为何**：零 LLM 成本、直接减轮次，笔记梳理/巡检任务收益最大。

### D3: 内容失效用 mtime/hash

READ 结果记「路径 + mtime/hash」；`file_write` 命中同一路径使该路径缓存失效。
- **为何**：只按路径去重会在「改写后回旧缓存」上制造精度 bug，与「精准」目标冲突；mtime/hash 成本可忽略。

### D4: Verifier 证据 = 交付物内容摘要

`_reflect_final` 的 prompt 拼入 `saved_paths` 每个交付物的「前 N 行内容摘要」，不发起工具调用、不加 LLM 轮次；保持纯裁判、`_REFLECT_FAIL_CONVERGE=3` 不变。
- **为何**：压「误收」——让 Verifier 核对实际数字/SQL/字段口径，而非猜候选文本。

### D5: 529 退避范围与次数

可重试状态码 = 529（过载）+ 429（限流）+ 5xx（502/503/504 瞬态），复用现有指数退避骨架：529/429 用 3 次（约 2s→6s→18s），5xx 用 5 次；400/401/2013/1026/1027 等确定性失败不重试。
- **为何**：529 语义就是「稍后重试」，429/5xx 同理重试有收益；确定失败重试只会重复撞墙烧轮次。

### D6: 组内降级不写新代码

group failover 已在 `chat_completion`（`llm_client.py:634-654`），成员抛异常即切下一个；V10 不改 group 代码，仅文档引导「Agent 绑 `type:"group"` 的 LLM 获得多模型降级」。
- **为何**：failover 已够用；健康度/轮换是过度设计，且铁律禁止硬门禁。

### D7: 铁律不变

全部软提示 / 本地静态检查 / 传输层 resilience，无静态硬门禁 / 阈值。

## Risks / Trade-offs

- [风险] 去重指针后模型可能不主动引用缓存，反而反复提示。→ 缓解：指针串带「已缓存，请引用之前结果」+ 路径，提示词明确「引用缓存不要重读」。
- [风险] mtime/hash 使去重键更重、读文件成本略增。→ 缓解：仅 READ 记 hash，SHELL/SEARCH 用规范化命令/query（无 hash）；hash 只对非超大文件计算。
- [风险] 529 退避重试可能拉长单轮耗时（最长 ~26s）。→ 缓解：3 次上限 + 指数退避，总等待有界；`cancel_check` 在每轮 sleep 前仍可中止。
- [风险] Verifier 拼交付物摘要会略增 prompt 长度。→ 缓解：只拼「前 N 行」摘要（N 温和，如 20），且仅拼 `saved_paths` 中确实存在的文件。
- [风险] 前置静态检查误报（把计划正文里提到的动作当成「要用」）。→ 缓解：只识别带协议前缀的动作（`SHELL:`/`READ:` 等），且命中仅软提示不阻断。

## Open Questions

- 去重指针的缓存大小上限（`query_cache` 是否按会话限条数）。
- Verifier 交付物摘要的 N（前多少行）取值。
- 529/429 退避 3 次是否足够（若集群长时间过载，3 次后仍失败是否交还「稍后重试」文案给用户）。
