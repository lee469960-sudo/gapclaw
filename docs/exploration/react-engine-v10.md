# react-engine-v10 — 最少轮次精准（规划前置验证 / 结果去重复用 / 验证收敛 + 529 韧性）

## Context

触发信号：MiniMax **529（2064 集群过载）**。目标从 v9 的「别再爆上下文（2013）」升级为「**用最少的轮次精准完成任务**」。

前序已修：
- **v8**：shell-only 误路由、`httpmcp_call`/`file_search` 接成真实动作。
- **v9**：文字任务降轮次（无依赖同轮批量）+ 控上下文（大结果落盘回显 + token 安全余量），压住 2013。

v10 收三个剩余维度（grill 多选：**规划前置验证 / 结果去重复用 / 验证收敛精度**）+ 一个正交项（**529 韧性**）。

铁律贯穿：**无任何静态硬门禁**。V10 全部改动为「本地静态检查 + 软提示 + 传输层 resilience」，不新增循环级计数器 / 阈值 / 硬停；循环仍只由 `FINAL` / 用户取消 / LLM 错误 / `max_iters` 结束。

## 现状与缺口（grill 结论）

### 三个维度

1. **规划前置验证 = 零验证**。`_apply_plan`（`runtime.py:1412`）只做「解析 PLAN → `parse_subtasks` → `_merge_subtasks` → 回显清单 → checkpoint」，**没有「计划动作是否在权限内 / 计划是否可执行」的检查**。Verifier `_reflect_final`（`runtime.py:475`）只在 `FINAL:` 判一次，不碰 PLAN。跑偏/越权的计划会先白跑一轮、再被 FINAL 驳回重规划——多烧轮次。
2. **结果去重复用 = 只有 MCP 有**。`query_cache`（`loop_state.py:28`，`{dedup_key:{path,tool,size}}`）由 `McpSessionManager` 维护，**只覆盖 MCP 查询**（`runtime.py:1150` 注释、`_is_cached_reference` `runtime.py:1578` 均为 MCP 专属）。READ 同文件 / SHELL 同命令 / SEARCH 同 query，模型重复执行时无任何缓存命中提示，重读、重跑、重进上下文——笔记梳理反复读同一批文件，是轮次 + 上下文双浪费。
3. **验证收敛精度 = Verifier 只看候选文本**。`_reflect_final` 给它的证据只有 goal / memory / `saved_paths`（**路径名，非内容**）/ 子任务清单 / 进度 / 候选回复；**不读交付物内容**。收敛侧已有 `_REFLECT_FAIL_CONVERGE=3`（`runtime.py:955`，连 3 次 FAIL 就接受）防死循环，prompt 也写了「排版措辞小瑕疵一律 PASS」压误拒；但「误收」（PASS 了其实错的答案）没有护栏——它看不到交付文件里的数字/SQL 对不对。

### 529 现状

- 529 是 `httpx.HTTPStatusError`（HTTP 状态码），**不是** `httpx.TransportError`。
- 现有退避 `_post_with_transport_retry`（`llm_client.py:719-752`，指数退避 2/4/8/16s + 25% jitter，5 次）**只对 `TransportError` 生效**（DNS/连接/代理重启），不管 529。
- 529 直接走 `except httpx.HTTPStatusError`（`llm_client.py:768`）→ `_is_sensitive_input_error` 为 False → **无重试、无退避**，`raise LLMHTTPError`。
- 到 runtime 层（`runtime.py:846-883`），`LLMHTTPError` 非 `LLMTransportError`，走 `llm_failures` 阈值 2，**`continue` 前无 sleep**，连试 2 次仍 529 就「任务已暂停」。
- **组内降级已实现**：`chat_completion` 的 group failover（`llm_client.py:634-654`）遍历 `members`，任一成员抛异常即 `continue` 换下一个——前提是 Agent 的 `llm_id` 绑的是一个 `type:"group"` 的 LLM。
- `format_llm_http_error`（`llm_client.py:470`）只处理 401/1026/1027/2013/400，**无 529/429/5xx 文案**。

529 语义是「集群过载，请稍后重试」，但客户端当前没有「稍后」这个动作。

## Requirements

### R1 — 规划前置验证（零 LLM 成本）

在 `_apply_plan` 解析 PLAN 时**同步做本地静态检查**（不额外调 LLM）：① 计划提到的动作（`SHELL:`/`READ:`/`WRITE:`/`SEARCH:`/`MCP:` 等）是否都在 `allowed_actions` 内；② 计划是否空 / 能否解析出子任务。命中 → 软 `coach_hint` 提示，**不阻断、不额外调用**。避免「计划要用没权限的工具 → 首轮执行失败 → 重规划」的浪费（v8 的 httpmcp/file_search 教训）。

### R2 — 工具结果去重复用

把 `query_cache` 去重从 MCP-only 扩展到 **READ / SHELL / SEARCH**。去重键 = 动作 + 目标（READ 用路径、SHELL 用规范化命令、SEARCH 用 query）。命中且内容未变时，回显「已缓存，请引用之前结果」指针（对齐 MCP 的 `_cached_reference` 形态），不再重读/重跑/重进上下文。

### R3 — 去重结果内容失效

READ 结果记「路径 + mtime/hash」；`file_write` 命中同一路径时使该路径缓存失效。避免「文件被改写后模型仍拿旧缓存」的精度 bug。

### R4 — 完成度复核交付物证据

`_reflect_final` 的 prompt 拼入 `saved_paths` 每个交付物的「前 N 行内容摘要」（文件已在磁盘/`saved_paths`，只多读几行拼文本，不发起工具调用、不加 LLM 轮次）。让 Verifier 核对实际数字/SQL/字段口径，压「误收」。保持纯裁判、不调工具、`_REFLECT_FAIL_CONVERGE=3` 不变。

### R5 — 可重试 HTTP 状态码退避重试

`chat_completion` 对「可重试 HTTP 状态码」——**529（过载）、429（限流）、5xx（502/503/504 瞬态）**——做指数退避重试（复用 `_post_with_transport_retry` 的 2/4/8/16s + jitter 骨架）：529/429 用 **3 次**（约 2s→6s→18s，累计 ~26s），5xx 用现有 5 次。**确定性失败（400/401/2013/1026/1027）不重试**。组内降级已存在（group failover），**不改 group 代码**，仅文档引导「Agent 绑 group 获得多模型降级」。

## Decisions

- **D1 前置验证零 LLM 成本**：本地静态检查（权限对齐 + 可解析），不额外调 LLM；命中软提示不阻断。理由：前置验证若吃一轮 LLM 就违背「最少轮次」。
- **D2 去重覆盖 READ/SHELL/SEARCH**：对齐 MCP 既有 `query_cache` 形态，零 LLM 成本、直接减轮次。
- **D3 内容失效用 mtime/hash**：READ 记「路径 + mtime/hash」，WRITE 命中即失效；只按路径去重会在「改写后回旧缓存」上制造精度 bug。
- **D4 Verifier 证据 = 交付物内容摘要**：拼 `saved_paths` 前 N 行，不发起工具调用、不加轮次；保持纯裁判 + `_REFLECT_FAIL_CONVERGE=3`。
- **D5 529 退避范围与次数**：529 + 429 + 5xx（502/503/504）退避重试；400/401/2013/1026/1027 不重试。529/429 3 次、5xx 5 次，复用现有指数退避骨架。
- **D6 组内降级不写新代码**：group failover 已在 `chat_completion`，只补文档引导（Agent 绑 group）；不新增 group 健康度/轮换（过度设计 + 铁律禁止硬门禁）。
- **D7 铁律不变**：全部软提示 / 本地静态检查 / 传输层 resilience，无静态硬门禁。

## 全链路改动

| 层 | 改动 |
|---|---|
| `runtime.py` | `_apply_plan` 加前置静态检查（R1）；READ/SHELL/SEARCH 去重判定 + WRITE 失效（R2/R3，复用 `query_cache`）；`_reflect_final` prompt 拼交付物内容摘要（R4） |
| `context_manager.py` | 去重命中「已缓存，请引用」指针的接收/回显（R2） |
| `system_prompt.py` | 去重指针用法说明（R2）；Verifier 证据（交付物摘要）语义引导（R4，如无需可不动） |
| `agent_tools.py` | READ/SEARCH 返回结果带「路径/query」标识，供去重键计算（R2/R3） |
| `llm_client.py` | 可重试 HTTP 状态码退避重试（R5）；`format_llm_http_error` 补 529/429/5xx 文案（R5） |

## Non-Goals

- 不改 `_run_modular` 循环本体、不加循环级门禁。
- 不做「额外一次 LLM 的规划校验」（D1：本地静态检查）。
- 不改 group failover 代码、不加 group 健康度/轮换（D6）。
- 不把 Verifier 变成「带工具」的复核（保持纯裁判，D4）。
- READ offset/limit 分页（v9 D5 遗留）不在本 v10 范围。

## 验证要点

1. 越权计划：PLAN 提到未授权动作 → coach hint 提示，且不阻断、不额外 LLM 调用。
2. 去重命中：重复 READ 同文件 / SHELL 同命令 → 回显「已缓存」指针，不重跑；WRITE 改写后 → 缓存失效、重读。
3. Verifier 能依据交付物内容摘要判 FAIL（如 SQL 数字对不上），减少误收；`_REFLECT_FAIL_CONVERGE=3` 收敛行为不变。
4. 529/429/5xx 触发退避重试（有 sleep、有次数上限）；400/401/2013/1026/1027 不重试直接报错。
5. 回归：现有测试 + v6/v7/v8/v9 需求不回归；无新增循环门禁 / 静态阈值。
