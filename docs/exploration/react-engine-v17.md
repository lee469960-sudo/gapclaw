# React Engine v17 — 空响应与 FINAL 截断（需求输入）

> Grill 已收敛（4 轮）。本文是 OpenSpec / 实现的需求输入。
>
> **状态：需求已锁定**（Q1=A / Q2=A / Q3=A / Q4=A / Q5=A / Q6=A / Q7=A / Q8=A / Q9=A / Q10=A / Q11=A / Q12=A / Q13=A / Q14=A）。

---

## 0. 铁律（不变）

- 无任何静态硬门禁 / 循环级计数器强制停止。
- 循环只由：① FINAL（经 Verifier）② 用户取消 ③ LLM 错误 ④ `max_iters` 结束。
- **有界请求级容错**（续写 / 抬输出预算）只发生在 `chat_completion` 内，不参与循环决策。

---

## 1. 问题与根因

| # | 现象 | 根因 |
|---|------|------|
| 1 | `模型组全部失败: LLM 响应缺少 content，且未返回可执行 tool_calls` | HTTP 200 空 content 在 `extract_chat_response_text` raise → 组内成员全失败 → `llm_failures` 连续 2 次暂停 |
| 2 | 轮次跑完任务未完成 | 撞 `max_iters` 且无被接受的 FINAL；本轮不新开抗空转，P0 修完后空 200 不再提前暂停即可 |
| 3 | FINAL 输出截断 | 不读 `finish_reason`；`allowed_out` 可压到 256；残篇被当完整回复 |

---

## 2. 已确认需求

### R1 — 空 HTTP 200 不是 LLM 失败

解析成功但无可执行产出（无非空 content、无可映射 tool_calls、非 MiniMax 纯 thinking）：

1. **不要 raise**；
2. 同请求内裁输入保证 `allowed_out ≥ 2048`，再打 1 次；
3. 仍空 → 返回空 `ChatResult` / `""`，循环走现有 empty-reply coach；
4. **不计入** `llm_failures`，**不换**组内下一个成员。

### R2 — `finish_reason=length` 最多续写 2 次

- 仅当 `finish_reason` 为 `length` / `max_tokens` 且已有文本时续写；
- 无 `finish_reason` 且已有文本 → **不**续写；
- 最多 2 次；每次同样保证输出预算；
- 成功 `stop` → 返回拼接全文。

### R3 — 2 次后仍 length：阻断 FINAL

返回拼接文本并带 `ChatResult.output_truncated=True`。循环侧即使含 `FINAL:` / 完成信号，**不得**进 Verifier / 结束；coach 后 `continue`。

### R4 — 残缺 native tool_calls 视同空

JSON 截断或全部 `_tool_call_to_step` 失败 → 走 R1 管道，**禁止执行**半截工具。

### R5 — P1 本轮范围

不新开抗空转设计。验收：空 200 不再「连续调用失败」暂停；续写不计循环轮次；真 `max_iters` 仍诚实 `_distill_final`。

---

## 3. 决策汇总

| # | 决策 |
|---|------|
| D1 | P0=空响应+截断（同根）；P1=副作用验收 |
| D2 | 空 200 → 软空回，非 LLM 失败 |
| D3 | 截断检测只认 `finish_reason=length` |
| D4 | 请求级最多 2 次续写；仍 length → 阻断 FINAL |
| D5 | 重试裁输入保 `allowed_out ≥ 2048` |
| D6 | MiniMax `reasoning_split` 保持开启 |
| D7 | 铁律不变 |

---

## 4. 明确不做

- 循环级硬门 / 强制 FINAL / 自动延长 `max_iters`
- 无 `finish_reason` 时对短文本启发式续写
- 关掉 `reasoning_split`
- 工具结果必须完整进上下文
- Web WS 500 字预览
- 抬默认 `max_iterations` / Ollama 预设

---

## 5. Acceptance Criteria

1. 组内三成员空 200 → 不再出现 `模型组全部失败: LLM 响应缺少 content…`；循环软空回继续。
2. `finish_reason=length` 的 FINAL：最多 2 次续写后完整；仍 length → 用户看不到「已完成但切半」的 FINAL。
3. 残缺 `tool_calls` 不执行。
4. 回归：MiniMax 纯 thinking（带 `reasoning_*`）仍软空回；真 4xx/5xx 仍组 failover + `llm_failures`。

---

## 6. 主改文件

- `apps/api/app/services/llm_client.py`
- `apps/api/app/services/agent_runtime/runtime.py`
- `apps/api/tests/`（新增）
