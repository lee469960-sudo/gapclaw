## ADDED Requirements

### Requirement: 空 HTTP 200 软空回

当 LLM 返回 HTTP 200 且解析成功，但无可执行产出（无非空 content、无可映射的 native tool_calls，且非仅含 reasoning 字段的纯思考轮）时，`chat_completion` MUST NOT 抛错。系统 MUST 在同一请求内裁剪输入以抬高输出预算（目标 `allowed_out ≥ 2048`）并重试一次；仍无产出时 MUST 返回空回复给循环，循环 MUST 走既有空回复软提示路径继续。该结果 MUST NOT 计入 LLM 连续失败计数，MUST NOT 触发模型组切换到下一成员。

#### Scenario: 空 content 不 raise

- **WHEN** 叶子模型返回 HTTP 200、`content` 为空且无可执行 `tool_calls`、亦无 reasoning 兜底字段
- **THEN** 不抛「LLM 响应缺少 content」类错误
- **AND** 同请求内抬输出预算重试一次后仍空则返回空回复

#### Scenario: 空 200 不换组员

- **WHEN** Agent 绑定模型组且当前成员返回空 HTTP 200（含重试后仍空）
- **THEN** 将该空回复作为成功结果返回给循环
- **AND** 不切换到组内下一成员
- **AND** 不抛「模型组全部失败: LLM 响应缺少 content…」

#### Scenario: 纯思考轮仍软空回

- **WHEN** MiniMax 等返回仅含 reasoning 字段、无 content / tool_calls 的思考轮
- **THEN** 返回空回复且不计入 LLM 失败
- **AND** MUST NOT 仅为抬预算而强制重试（与既有软空回语义一致）

#### Scenario: 真错误仍 failover

- **WHEN** 成员返回 HTTP 4xx/5xx 或传输失败等真错误
- **THEN** 仍按既有模型组 failover 与 `llm_failures` 阈值处理

### Requirement: finish_reason=length 请求级续写

当响应 `finish_reason` 为 `length` 或 `max_tokens` 且已有可见文本时，`chat_completion` MUST 在同一请求内拼接 assistant 残篇并续写，最多 2 次；每次续写 MUST 保证足够输出预算（目标 `allowed_out ≥ 2048`）。续写成功（非 length）时 MUST 返回拼接后的完整文本。无 `finish_reason`（或非 length 类）且已有文本时 MUST NOT 启发式续写。

#### Scenario: length 续写拼接

- **WHEN** 首次响应 `finish_reason=length` 且 content 非空
- **THEN** 在同一次 `chat_completion` 内最多续写 2 次并拼接全文返回

#### Scenario: 无 finish_reason 不续写

- **WHEN** 响应已有文本但无 length / max_tokens 类 `finish_reason`
- **THEN** 不发起续写，按完整回复返回

#### Scenario: 续写成功返回全文

- **WHEN** 续写过程中某次响应不再是 length
- **THEN** 返回已拼接的完整文本，且不标记为截断

### Requirement: 截断输出阻断 FINAL

当请求级续写用尽后响应仍为 length 截断时，系统 MUST 将拼接文本交回循环并标记为输出截断。循环即使在该文本中识别到 `FINAL:` 或完成信号，MUST NOT 将其送入完成度复核或作为任务完成结束；MUST 注入软提示后继续下一轮。

#### Scenario: 截断残篇不得结束任务

- **WHEN** 续写 2 次后仍 `finish_reason=length` 且文本含 `FINAL:`
- **THEN** 循环不进入完成度复核、不将任务标为完成
- **AND** 注入「输出被截断」类软提示后继续

#### Scenario: 截断后下一轮可正常 FINAL

- **WHEN** 上一轮因截断被阻断，下一轮返回完整（非截断）FINAL
- **THEN** 按既有 FINAL / 复核路径正常结束

### Requirement: 残缺 native tool_calls 禁止执行

当 native `tool_calls` 因截断导致 JSON 残缺、或全部无法映射为可执行步骤时，系统 MUST 将其视同无可执行产出（走空响应软容错管道），MUST NOT 执行任何半截工具调用。

#### Scenario: 截断 tool_calls 不执行

- **WHEN** 响应含 `tool_calls` 但参数 JSON 截断或全部无法映射
- **THEN** 不执行任何工具
- **AND** 按空 / 截断容错路径处理（抬预算重试或软空回）

#### Scenario: length 截断的可解析 tool_calls 亦不执行

- **WHEN** `finish_reason=length` 且存在可映射的 tool_calls
- **THEN** MUST NOT 执行这些可能不完整的工具调用
- **AND** 按截断容错路径处理

## MODIFIED Requirements

### Requirement: 模型组解析环检测

`chat_completion` / `test_llm_chat` 解析 `type:"group"` 时 MUST 携带 `visited` 集合（按 LLM id）与深度上限（默认 8 层）；命中已访问 id 或超过深度上限时 MUST 抛出可读错误（如「模型组存在循环引用或嵌套过深」），MUST NOT 无限递归。单层叶子成员全部**真失败**（HTTP/传输/缺 choices 等）时 MUST 抛单层「模型组全部失败: <底层错误>」，不嵌套叠加前缀。空 HTTP 200 / 软空回 MUST NOT 视为成员失败。

#### Scenario: 自引用 / 循环引用组

- **WHEN** 解析的组存在自引用或 A→B→A 循环
- **THEN** 抛出「模型组存在循环引用或嵌套过深」
- **AND** 不无限递归、不产生上千层嵌套错误消息

#### Scenario: 嵌套过深

- **WHEN** 组嵌套层级超过深度上限
- **THEN** 抛出「模型组存在循环引用或嵌套过深」

#### Scenario: 单层成员全失败

- **WHEN** 组的所有叶子成员都真失败（如 529）
- **THEN** 抛出单层「模型组全部失败: <底层错误>」
- **AND** 错误消息不含重复叠加的「模型组全部失败」前缀

#### Scenario: 顺序切换语义不变

- **WHEN** 组某成员请求真失败
- **THEN** 仍按既有顺序切换到下一成员（不改变 group failover 语义）

#### Scenario: 空 200 不计入成员失败

- **WHEN** 组内某成员返回空 HTTP 200（无可执行产出，含抬预算重试后仍空）
- **THEN** 将该空回复作为成功返回
- **AND** 不切换下一成员、不抛「模型组全部失败」
