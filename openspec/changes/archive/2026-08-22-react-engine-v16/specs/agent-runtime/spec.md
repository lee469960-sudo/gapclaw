## ADDED Requirements

### Requirement: 完成信号软转换

当模型连续 2 轮输出正向完成声明（含「已完成/任务完成/最终交付/已交付/无需再调用工具」等关键词、本轮无工具调用、非疑问句）时，引擎 MUST 调用一次 LLM 确认该文本是否为完成声明；确认后 MUST 将文本作为 `FINAL` 候选送入完成度复核（`_reflect_final`）。该转换 MUST 只认正向完成、MUST NOT 识别负向「无法完成/无法继续/无法连接」；MUST NOT 新增确定性停止门（循环仍由 `FINAL` / 用户取消 / LLM 错误 / `max_iters` 结束）。

#### Scenario: 连续两轮完成声明软转换

- **WHEN** 连续 2 轮输出正向完成声明文本且无工具调用
- **THEN** 引擎用一次 LLM 判断「是否为完成声明」
- **AND** 确认为完成声明后，把文本作为 `FINAL` 候选送入完成度复核

#### Scenario: 否定与疑问不触发

- **WHEN** 文本含「无法完成/不能/吗/？」等否定或疑问
- **THEN** 不触发软转换，仍走既有纯文本提示路径

#### Scenario: 转换后仍受复核约束

- **WHEN** 完成声明被软转换为 `FINAL` 候选
- **THEN** 仍经 `_reflect_final` 复核，FAIL 时按定向补处理

### Requirement: 需求感知完成度复核

完成度复核器 MUST 现场把任务目标（goal）逐条拆成核对项（列/字段/口径/补充说明），逐项判 PASS/FAIL；对数据准确性任务，字段口径、数字、映射关系 MUST 精确核对，仅排版与措辞可豁免（不得再以「小瑕疵」一律 PASS）。复核 FAIL 时 MUST 只定向补缺失项，保留已完成子任务与已写文件，不推翻完成态。

#### Scenario: 逐条核对

- **WHEN** 复核器收到 `FINAL` 候选
- **THEN** 逐条列出核对项并判 PASS/FAIL，FAIL 项进入修复清单

#### Scenario: 字段口径缺失判 FAIL

- **WHEN** 交付物缺失「渠道名称映射」等字段口径
- **THEN** 判 FAIL 并列入修复清单

#### Scenario: 定向补不推翻完成态

- **WHEN** 复核 FAIL
- **THEN** 修复清单只针对失败项，已完成子任务与已写文件保留

### Requirement: 已完成清单注入

引擎 MUST 每轮在任务上下文稳定层回显「已完成子任务（勾选）+ 已写文件路径 + 进度后 N 条 + 已尝试工具摘要」；resume 时额外注入「上次执行到此、还差什么」。该清单 MUST 由引擎零 LLM 拼（复用 progress_lines / saved_paths / subtasks / tool_call_tally / query_cache），MUST NOT 被 `trim_tool_results` 裁剪。

#### Scenario: 每轮回显清单

- **WHEN** 构建任务上下文（含 resume 后首轮）
- **THEN** 注入已完成清单（子任务 + 文件 + 进度 + 已尝试工具）

#### Scenario: 清单不被裁剪

- **WHEN** 上下文周期性裁剪
- **THEN** 已完成清单保留，不被裁剪

### Requirement: MCP 工具调用去重与大结果取回指引

所有 MCP 工具调用 MUST 按「工具名 + 归一化参数」作为 key 进入查询缓存；其中 `execute_ads_sql` 的参数按 SQL 归一化（去空白/大小写/尾分号，`LIMIT`/`OFFSET` 不同视为不同）；命中时 MUST 回显「该结果已缓存/已落盘 `path`，请 READ 取回，勿重跑」；结果过大落盘后 MUST 明确回显完整取回路径，而非只回显截断片段。

#### Scenario: 相同 MCP 工具调用命中缓存

- **WHEN** 模型再次调用与已执行 MCP 工具「工具名 + 归一化参数」相同的调用
- **THEN** 命中缓存，回显「已缓存/已落盘 `path`，请 READ 取回」

#### Scenario: 相同 SQL 命中缓存

- **WHEN** 模型再次调用与已执行 SQL 归一化后相同的 `execute_ads_sql`
- **THEN** 命中缓存，回显「已缓存/已落盘 `path`，请 READ 取回」

#### Scenario: 大结果落盘回显路径

- **WHEN** 结果过大落盘
- **THEN** 回显完整取回路径，供 READ/SHELL 取回
