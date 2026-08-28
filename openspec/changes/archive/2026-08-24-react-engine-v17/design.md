## Context

动机见 `proposal.md`「Why」。需求输入见 `docs/exploration/react-engine-v17.md`。

相关现状与约束：

- **铁律**：循环只由 FINAL / 取消 / LLM 错误 / `max_iters` 结束；请求级容错（续写、抬 `max_tokens`）不参与循环决策（同 v5 对外部 API 有界重试的边界）。
- 现状：`extract_chat_response_text` 对空 content 无 tool_calls 无 reasoning 时 **raise**；组 failover 把任意 `Exception` 当成员失败 → 用户看到 `模型组全部失败: LLM 响应缺少 content…`；主循环 `llm_failures` 阈值 2 暂停任务。
- 现状：不读 `finish_reason`；`fit_messages_to_context` 的 `allowed_out` 底为 256；长上下文下 FINAL 易被 length 截断却当完整回复发出。
- MiniMax `reasoning_split=True` 保持开启；纯 thinking（带 `reasoning_*`）已有软空回路径，本设计不改请求形状。

## Goals / Non-Goals

**Goals:**

- 空 HTTP 200 → 软空回，不提前杀死任务、不误伤组 failover。
- `finish_reason=length` → 请求内最多 2 次续写；仍截断则阻断 FINAL。
- 残缺 / length 截断的 tool_calls → 永不执行半截工具。
- 重试时腾出输出窗口（`allowed_out ≥ 2048`）。

**Non-Goals:**

- 循环级硬门、强制 FINAL、自动延长 `max_iters`。
- 无 `finish_reason` 时对短文本启发式续写。
- 关掉 `reasoning_split`；工具结果必须完整进上下文；改 WS 500 预览 / 默认迭代上限。

## Decisions

### D1: 一条请求级管道放在 `chat_completion` 叶子路径

空重试、length 续写、残缺 tool 拒绝全部在叶子 `chat_completion` 内完成；组递归只把「成功返回」（含软空）或「真异常」向上传。

- **为何**：组 failover 语义保持「真错误才换人」；空 200 若 raise 会被误当成成员失败。
- **备选**：在 runtime 循环里做续写 —— 被否，会占循环轮次、违反「续写不计循环轮次」与铁律边界。

### D2: 空 → 抬预算重试 1 次 → 软空 `ChatResult`，不 raise

取消「缺少 content」硬 raise；MiniMax 纯 thinking 仍直接软空（不强制抬预算重试）。

- **为何**：空多为输出预算过紧或瞬时空回合；raise 只会连坐整组并烧 `llm_failures`。
- **备选**：空仍算成员失败换组 —— 被否（grill Q2=A / Q7=A）。

### D3: 只认 `finish_reason=length|max_tokens` 续写，上限 2

无 finish_reason 的已有文本不续写；续写消息追加 assistant 残篇 + 用户「从断点继续」提示。

- **为何**：避免完整短答再被续出第二段；上限 2 与有界 API 容错一致。
- **备选**：启发式「话说到一半」续写 —— 被否（grill Q10=A）。

### D4: `ChatResult.output_truncated` + runtime 阻断 FINAL

2 次后续写仍 length → 返回拼接文本并 `output_truncated=True`；`_run_modular` 在解析 FINAL / 完成信号之前检查该标记，coach 后 `continue`。

- **为何**：用户不得收到「已完成但话说到一半」；残篇仍可回灌上下文供下一轮续写。
- **备选**：丢弃残篇当空回 —— 被否（grill Q13=A 保留文本但阻 FINAL）。

### D5: `fit_messages_to_context(..., min_allowed_out=2048)` 用于重试/续写

空/length 重试路径传入更高输出地板，必要时更狠裁历史，使 `allowed_out` 尽量 ≥ 2048。

- **为何**：不腾窗口的「抬 max_tokens」是空操作（现状底 256）。
- **备选**：全局抬高所有请求的 floor —— 被否，以免挤压正常长上下文首轮（grill Q14=A 仅重试）。

### D6: length / 不可映射的 tool_calls 一律不执行

可映射但 `finish_reason=length` 的 tool_calls 也拒绝执行（可能半截），走空/截断管道。

- **为何**：半截 JSON / 半截多工具调用执行有破坏性风险。
- **备选**：执行已解析成功的子集 —— 被否（grill Q12=A）。

## Risks / Trade-offs

- [风险] 空重试 + 续写增加单轮 LLM 调用次数与延迟。→ 缓解：上限明确（空 1 次、length 2 次）；仅在触发条件命中时发生。
- [风险] 裁输入保输出可能丢掉较早工具结果。→ 缓解：仅重试路径更狠裁；archive / RECALL 与既有 materialize 仍可用。
- [风险] 部分网关不返回 `finish_reason`，截断无法检测。→ 缓解：按决策不启发式续写；接受该网关下无法保证「完全输出」。
- [取舍] 软空回后可能更快撞 `max_iters`。→ 缓解：P1 明确接受诚实 `_distill_final`；不新开抗空转。

## Migration Plan

- 纯行为变更，无 DB / API schema 迁移。
- 部署后：空 200 不再暂停；观察 length 续写日志与截断 coach 频率。
- 回滚：恢复空 raise + 不读 finish_reason 的旧 `chat_completion` / runtime 分支即可。

## Open Questions

- `allowed_out ≥ 2048` 的具体数字是否按模型窗口再调 —— 实现可微调，不改需求语义。
