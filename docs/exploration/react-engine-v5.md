# React Engine v5 — 需求整理（OpenSpec 需求输入）

> 本文是 grill-me 阶段产出的**需求输入**，用于进入 OpenSpec 之前对齐范围与验收口径。
> 只陈述「要解决什么问题、怎么判定做对了」，不写实现细节；实现方案由后续 OpenSpec 产出。
>
> 背景驱动：MiniMax 返回 `400 invalid params (2013)`，报错文案「请检查上下文长度、max_tokens，或缩短会话后重试」是引擎在 `format_llm_http_error` 里**硬编码的误读**（`llm_client.py:431-432`），并非 MiniMax 真实语义。外部查证：MiniMax **2013 = `invalid params`（请求形状错误）**，token 超限是 `1039`。grilling 收敛后发现真实根因是「原生 tool_calls 路径留下孤儿 `assistant(tool_calls)`」，次要隐患是 token 估算粗糙。
>
> **状态：需求已锁定**（Q1=C / Q2=A / Q3=A / Q4=A / Q5=A / Q6=A / Q7=C / Q8=B）。

---

## 0. 铁律（最高优先级，贯穿全部需求）

> **无任何静态硬门禁。**
>
> - 不新增阈值 / 计数器 / 确定性强制停止。
> - 循环只由四类事件结束：① 模型输出 `FINAL:`/`done`；② 用户取消；③ LLM 错误；④ `max_iters` 预算耗尽。
> - 所有「纠正」走软 LLM 判断 + coach hint，不硬停。
> - 能删的冗余代码一律删除（删除优先于新增）。
>
> **V5 新增一条边界（Q3=A）**：「有界降级重试」是对**外部 LLM API 400** 的有限容错，**不是循环门禁**。重试上限（1–2 次）只作用于单次 `chat_completion` 请求，不参与循环内「是否/何时做工具调用」的决策；循环结束条件仍只有上述四类。降级重试、回退文本协议、发送前不变量均不引入任何循环级计数器 / 阈值 / 硬停。

---

## 1. 已确认需求（3 条：R1–R3）

### R1 — 原生 tool_calls 路径不得留下「孤儿 assistant(tool_calls)」

**问题**：`runtime.py:865` 无条件 `push_assistant_native(...)`（含 `tool_calls`），但三个控制流分支不补对应的 `role:tool`，导致下一轮 `cm.messages` 末尾挂着**无跟随 role:tool 的 `assistant(tool_calls)`**。MiniMax 严格规则「tool 结果必须紧跟对应 tool_call」被违反 → `400(2013)`。

三个已知触发分支（审计确认）：
1. **FINAL 与工具同轮**：模型同轮既发 `done`（原生 final_step）又发其它 tool_call，其它 tool_call 被 `skipped` 丢弃、不补 role:tool（`runtime.py:878-896`）。
2. **Verifier 判 FAIL**：`cm.add_coach_hint(...)` + `continue`，不 push 任何 role:tool（`runtime.py:920-921`）。
3. **空名解析**：`native=True` 但所有 tool_call 解析出空名，只推 assistant 不推 role:tool 就 continue（`runtime.py:925-974`）。

**确认口径**：

- **修复手法（Q6=A）**：当一条 `assistant(tool_calls)` 已推入、但本轮将以「不执行这些工具」收尾时，为每个未被执行的 `tool_call_id` 回填一条 `role:tool`，content 用占位（如「（该工具调用已跳过）」），保证原生配对完整、顺序正确。
- **修复层级（Q7=C）**：**源头补全**（修好上述三个已知分支）+ **发送前不变量**（`normalize_chat_messages` / `fit_messages_to_context` 出口检查「不以 orphan `assistant(tool_calls)` 结尾」，发现即修复），双保险杜绝 2013 复发。

### R2 — MiniMax 2013 降级重试兜底

**问题**：当前 2013 直接 `raise RuntimeError`（`llm_client.py:701`）；主循环 `llm_failures >= 2` 才终止，单次 400 会 `continue` 用**相同上下文重发**（`runtime.py:844-849`）→ 第二轮必然再撞同一 400。

**确认口径（Q2=A / Q5=A / Q8=B）**：

- 降级重试**仅在 `chat_completion` 内单请求发生**，不改循环状态；全部重试失败后照常 `raise`，主循环照走 `llm_failures` 逻辑。
- **重试序列**：先「减 `max_tokens` + 裁剪上下文」重试一次；仍失败则「去掉 `tools` 回退文本协议」重试一次；再失败才抛错终止。
- **回退文本协议时注入 coach hint（Q8=B）**：「原生工具调用失败，已切换文本协议，请改用 `SHELL:`/`MCP:` 等标记继续」——让模型从原生 tool_calls 切到文本标记，避免去掉 `tools` 后模型仍尝试发 tool_calls 或空转。

### R3 — token 估算保守化

**问题**：`estimate_tokens = len(text)//2`（`llm_client.py:266`）对中文系统性低估（中文约 1 token/字符，而非 2 字符/token），且不数 `tools` schema 字符、不数 `reasoning_split` 开销。

**确认口径（Q4=A）**：

- **保守字符估算，无新依赖**：
  - CJK 感知权重（中文按 ~1 token/字符、ASCII 按 ~4 字符/token 混合估算）；
  - 把 `tools` schema 的字符开销计入 `used`；
  - 把 MiniMax `reasoning_split` 的额外输出开销计入预留；
  - 全局安全余量（如 `budget = int(budget * 0.85)`），宁多裁不少裁。

---

## 2. 关键架构决策

| # | 决策 | 说明 |
|---|------|------|
| D1 | 形状硬化为主 + 估算保守兜底 | Q1=C；2013 主因是请求形状（orphan tool_calls），估算缺陷是次因（真超窗） |
| D2 | 2013 降级重试序列 | 减 `max_tokens` → 回退文本协议 → 干净终止（Q2=A） |
| D3 | 有界重试非门禁 | 外部 API 容错，不参与循环决策（Q3=A） |
| D4 | 保守字符估算 | CJK 权重 + 计入 `tools`/`reasoning_split` + 安全余量，无新依赖（Q4=A） |
| D5 | 单请求内降级 | 降级仅在 `chat_completion` 内，主循环状态不变（Q5=A） |
| D6 | 补空 role:tool | 为未执行的 tool_call_id 回填占位 role:tool（Q6=A） |
| D7 | 源头补全 + 发送前不变量 | `continue` 前补 role:tool + `normalize`/`fit` 出口禁止 orphan `assistant(tool_calls)`（Q7=C） |
| D8 | 回退伴随 coach hint | 回退文本协议时软提示模型切换标记（Q8=B） |

---

## 3. 边界条件

| 边界 | 约束 |
|------|------|
| 铁律 | 无任何静态硬门禁；有界重试只作用于单请求，不新增循环级计数器/阈值/硬停 |
| 2013 语义 | MiniMax `2013` = invalid params（请求形状）；`1039` = token 超限。引擎错误文案不得再把 2013 解读为「上下文过长」 |
| 重试上限 | 单请求内最多 2 次降级重试（减 max_tokens 1 次 + 回退文本协议 1 次），失败即 raise |
| 回退文本协议 | 去掉 `tools` 重发时，同步注入 coach hint 提示切换标记 |
| 补空 role:tool | 占位 content 非空（如「（该工具调用已跳过）」），避免与「空结果」语义混淆 |
| 发送前不变量 | 出口消息不得以 orphan `assistant(tool_calls)` 结尾；发现即补空或整组移除 |
| 估算 | 字符估算：CJK ~1 token/字符、ASCII ~4 字符/token；计入 `tools` 字符与 `reasoning_split` 开销；budget 乘安全余量 0.85 |
| 多 system | 归一化合并连续 system，coach hint 走 `_replace_layer` 原地替换，不得产生多条 system |
| content 非 null | 带 tool_calls 的 assistant `content` 恒为字符串（无文本时 `""`），不得为 null |

---

## 4. Acceptance Criteria

1. **R1**：三个已知分支（FINAL 同轮 / Verifier FAIL / 空名解析）后，`cm.messages` 不再以 orphan `assistant(tool_calls)` 结尾；发送前不变量保证任何路径都不会发出 orphan（补空 role:tool 或整组移除）。
2. **R2**：MiniMax 返回 2013（或任何上下文/参数类 400）时，`chat_completion` 先减 `max_tokens` + 裁剪重试一次；仍失败则去掉 `tools` 回退文本协议并注入 coach hint 重试一次；再失败才抛错。主循环 `llm_failures` 逻辑不变。
3. **R3**：`estimate_tokens` 对中文不再系统性低估（CJK 感知）；`tools` schema 与 `reasoning_split` 开销计入预算；`budget` 带安全余量。
4. **回归**：纯文本协议路径、非 MiniMax provider 路径行为不变；`build_tool_schemas` 仍不发射 `strict`/`tool_choice`/`parallel_tool_calls`/`stream_options`；多 system 仍被合并；`content` 仍非 null。

---

## 5. 遗留项（交给 OpenSpec 定实现，非需求分歧）

1. token 估算的**具体系数与余量**（CJK/ASCII 权重、`budget × 0.85` 还是固定 reserve），属实现细节，OpenSpec 定。
2. `max_context_tokens` 默认 `128000` 与 MiniMax-M3 实际可用窗口 / API tier 上限是否需校正，属配置层问题，OpenSpec 校验时可定（MiniMax-M3 官方宣称 1M，但 tier 可能更低；`128000` 目前偏保守，安全）。
3. 「减 `max_tokens`」的具体阶梯（如 8192→4096→2048）与「裁剪上下文」的粒度（复用 `slim_messages_for_sensitive_retry` 还是新逻辑），属实现细节。

---

## 6. 相关文档

- 上一轮需求（R1–R7，含 role:tool 原生回填 / 成组裁剪）：[`react-engine-v4.md`](react-engine-v4.md)
- 引擎内部地图（现状）：[`../react-engine.md`](../react-engine.md)
- MiniMax 错误码：2013 = invalid params；1039 = token 超限（外部查证，见会话记录）
