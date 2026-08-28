## ADDED Requirements

### Requirement: 非 FINAL 退出前持久化最新运行状态

当运行以非 FINAL 方式退出时（预算耗尽达到 `max_iters` 且存在未完成子任务、或 LLM 连续调用失败），引擎 MUST 在返回前持久化最新运行状态——包括已落盘 MCP 结果清单、查询去重缓存、进度行与已保存路径——而非仅持久化最后一次 PLAN 时的旧状态。续跑 MUST 复用同一 `run_ts` 产物目录并恢复这些状态，使模型能识别已产出中间产物、命中已缓存查询、延续进度，不重复查询。

#### Scenario: 预算耗尽时持久化最新状态

- **WHEN** 长任务因达到 `max_iters` 退出，且存在未完成的子任务
- **THEN** 返回前持久化当前 `mcp_results`、`query_cache`、`progress_lines`、`saved_paths`
- **AND** 续跑能恢复这些状态（而非仅回到最后一次 PLAN 时的旧状态）

#### Scenario: LLM 连续失败时持久化最新状态

- **WHEN** LLM 连续调用失败导致运行退出
- **THEN** 返回前持久化当前运行状态，且退出文案与真实持久化一致

#### Scenario: 续跑恢复非 FINAL 退出后的落盘状态

- **WHEN** 从一次非 FINAL 退出后续跑
- **THEN** 已落盘的 MCP 结果回填进度块（「已缓存 MCP 结果 …」），查询去重缓存生效，模型不再重复查询已缓存数据

### Requirement: 动态软提示与进度块不被截断丢弃

当 system 层合并后超出上限需要裁剪时，引擎 MUST 优先裁剪静态目录层（工具目录、技能快照等），而教练提示（`coach_hint`）与进度块（`progress_block`）MUST 在裁剪后仍完整保留，保证卡死检测、预算告急、完成度反思等软提示与「勿丢失」产物路径在长任务 / 大目录场景下不被丢弃。

#### Scenario: 大目录下软提示仍保留

- **WHEN** 工具目录与技能快照使 system 块超出上限
- **THEN** 裁剪静态目录层，教练提示完整保留

#### Scenario: 进度块路径不被丢弃

- **WHEN** 进度块记录了已落盘产物路径，且 system 块超限
- **THEN** 进度块完整保留，产物路径不因裁剪丢失

### Requirement: 完成度复核连续拒绝后收敛

对候选 FINAL 的完成度复核连续返回失败（FAIL）时，引擎 MUST NOT 无限空转到预算耗尽。连续拒绝达到阈值后，引擎 MUST 收敛——接受当前候选（记录并呈现被拒原因）或强制停止——而非继续消耗完整轮次直至耗尽预算丢弃（可能正确的）最终答案。

#### Scenario: 连续拒绝后接受候选

- **WHEN** 完成度复核对候选 FINAL 连续拒绝达到阈值
- **THEN** 引擎接受当前候选并收尾，同时记录 / 呈现被拒原因供用户观察

#### Scenario: 拒绝循环不空转到预算耗尽

- **WHEN** 完成度复核持续返回 FAIL
- **THEN** 引擎在阈值内收敛，不空转至 `max_iters` 耗尽后才丢弃最终答案

### Requirement: 复核拒绝时回灌修复清单

完成度复核返回失败时，引擎 MUST 把复核产出的修复清单 / 修订计划（`fix_list` / 修订后的 PLAN）随教练提示一并回灌给模型，使模型拿到具体的缺失项与修正方向，而非仅「任务尚未完成」的笼统提示。

#### Scenario: 复核失败回灌修复清单

- **WHEN** 完成度复核返回 FAIL 且产出具体修复清单 / 修订计划
- **THEN** 教练提示包含这些具体修复项 / 修订计划，而非仅笼统的缺失提示

## MODIFIED Requirements

### Requirement: 原生 tool_calls 的结果以 role:tool 回填并全程保持配对

当模型以原生 `tool_calls` 返回工具调用时，每个调用的结果 MUST 以 `role:tool` + 匹配的 `tool_call_id` 回填，而非 `role:user`；文本协议回复仍以 `role:user` 回填。原生 `tool_calls` MUST 直接生成带 `tool_call_id` 的工具步骤（单一来源，不二次文本解析）。归一化链路 MUST 透传 assistant 的 `tool_calls` 与 tool 消息的 `tool_call_id`；上下文裁剪时 MUST 将 `assistant(tool_calls)` 及其后连续的 `role:tool` 消息作为一个原子组整组删除，不得拆散。当某条原生 `tool_calls` 因权限未启用被阻止、或因 FINAL 同轮被跳过而未执行时，引擎 MUST 仍为其回填一个合成 `role:tool` 结果（明确「未执行」原因），保证每条 assistant `tool_calls` 都有配对的 tool 消息，避免下一轮协议校验失败。

#### Scenario: 原生调用结果 role:tool 回填

- **WHEN** 模型返回原生 `tool_calls` 且工具执行完成
- **THEN** 结果以 `role:tool` + 匹配 `tool_call_id` 回填到上下文

#### Scenario: 文本协议回复仍 role:user 回填

- **WHEN** 模型以文本协议（无 `tool_calls`）返回工具调用
- **THEN** 结果以 `role:user` 回填，行为不变

#### Scenario: 归一化透传配对字段

- **WHEN** 含原生配对的上下文经 `normalize_chat_messages` / `fit_messages_to_context`
- **THEN** assistant 的 `tool_calls` 与 tool 消息的 `tool_call_id` 字段不丢失

#### Scenario: 成组裁剪不孤立 tool 消息

- **WHEN** 上下文超预算需要裁剪
- **THEN** `assistant(tool_calls)` 与其后连续的 `role:tool` 消息整组删除，不出现无对应 tool_call 的孤立 tool 消息

#### Scenario: 被阻止或跳过的原生调用也回填合成结果

- **WHEN** 原生 `tool_calls` 中的某条因权限未启用被阻止、或因 FINAL 同轮被跳过而未执行
- **THEN** 引擎仍为其回填一个合成 `role:tool` 结果（明确「未执行」原因）
- **AND** 下一轮发送给模型的消息中，每条 assistant `tool_calls` 均有配对的 tool 消息，不触发协议校验失败
