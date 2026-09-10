## Why

ReAct 任务会在已有足够证据时，因模型生成的 `pending` PLAN 子任务或散文式“再复核”继续循环。尤其是已缓存/已物化的 MCP 结果被重复请求时，系统最终只能以资源保护硬停结束，既不能交付业务等价的条件性结论，也没有清晰说明真正未验证的内容。

## What Changes

- 将 LLM 自主生成的 PLAN 子任务降为执行建议：它们不再单独阻止候选 FINAL；只有原始用户目标的未满足可验证需求，或经运行时校验的证据缺口可以阻止交付。
- 让完成度复核器输出结构化证据缺口卡片；每张卡片必须声明目标需求、缺失证据、未执行工具动作和预期判定条件。
- 在运行时校验缺口卡片，并按规范化工具签名和已有/已物化证据去重。无效、已满足、已执行或可由缓存读取的缺口只作为说明，不阻止 FINAL。
- 对每个有效缺口最多执行两次具有不同规范化签名的取证动作；只读/分析任务随后以条件性结论收尾，避免重复推理。
- 按实际工具动作进行风险分层。写入、删除、外发、部署、支付及无法静态判断的 Shell 命令，必须有执行证据或用户确认；未解决时请求确认而非自动宣称完成。

## Capabilities

### New Capabilities

<!-- None. -->

### Modified Capabilities

- `agent-runtime`: Define bounded, evidence-aware finalization and verifier-gap handling for ReAct runs, including risk-aware escalation.

## Impact

- Affects `apps/api/app/services/agent_runtime/runtime.py`, tool/action normalization and safety classification helpers, and the ReAct runtime tests.
- Keeps MCP query caching/materialization and its duplicate-call protection; does not change MCP server protocols.
- Changes only terminal and verifier-loop behavior. Existing user-visible task steps gain explicit reasons when a gap is accepted, exhausted, or requires confirmation.
