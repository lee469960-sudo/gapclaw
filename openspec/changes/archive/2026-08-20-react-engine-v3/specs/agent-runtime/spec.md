## ADDED Requirements

### Requirement: 同一 MCP 工具大量调用时注入映射蒸馏软提示

单次运行内 MUST 按 `mcp:<tool_name>` 聚合同一 MCP 工具的成功调用次数（`<tool_name>` 从 `MCP:` 行提取的真实工具名，而非笼统的协议名），并与其调用参数是否相同无关；达到 4 次起、每 +2 次注入一条「映射蒸馏」软提示，指引模型把已确认的 `view→字段/口径` 映射蒸馏进 PLAN 或落盘，而非逐个 describe 大量资源。该提示 MUST NOT 进行硬门禁或中断循环；模型输出新 PLAN（含完成度复核后的 Replanner 修订）时 MUST 重置该计数。

#### Scenario: 达到阈值注入蒸馏提示

- **WHEN** 同一 MCP 工具在单次运行内被成功调用累计到第 4 次，并在此后每 +2 次（第 6、8…次）
- **THEN** 引擎注入一条「映射蒸馏」软提示，指引把 view→字段/口径映射蒸馏进 PLAN 或落盘
- **AND** 不产生任何硬性计数中断或循环终止

#### Scenario: 不同参数聚合计数

- **WHEN** 同一 MCP 工具以不同参数被多次调用（如逐个 describe 不同 view）
- **THEN** 这些调用按工具名聚合计数，而非按精确参数各自独立

#### Scenario: 新 PLAN 重置计数

- **WHEN** 模型输出新的 PLAN（或完成度复核后 Replanner 输出修订计划）
- **THEN** 该工具调用计数重置为零，后续调用重新累计

### Requirement: 超大 MCP 结果物化时内联结构摘要

当 MCP 结果因过大被物化到 `mcp_result_N.json` 时，若结果为 JSON，引擎 MUST 在「已写入」通知里内联一段结构摘要（顶层 key + 数组元素字段名，≤320 字符），使模型无需读取完整文件即可取得字段名以建立映射。非 JSON 或空 payload 时 MUST NOT 追加摘要行。

#### Scenario: JSON 对象结果提取顶层 key

- **WHEN** 物化的结果为 JSON 对象
- **THEN** 通知内联一段结构摘要，含顶层 key 名（及前若干数组元素的字段名）

#### Scenario: JSON 数组结果提取首元素字段名

- **WHEN** 物化的结果为非空 JSON 数组
- **THEN** 通知内联一段结构摘要，含数组项数与首元素字段名

#### Scenario: 非 JSON 或空 payload 不加摘要行

- **WHEN** 物化结果非 JSON，或为空数组 / 空对象
- **THEN** 不产生结构摘要行，通知保持原有形态

### Requirement: 绑定 MCP 时系统提示含资源映射指引

当 agent 绑定至少一个 MCP 时，系统提示 MUST 包含「资源映射」指引：原始 list/describe/query 结果可能被截断或落盘（过大时写入 `mcp_result_*.json`），确认的 `view→字段/口径` 映射必须蒸馏进 PLAN 或落盘文件，不要逐个 describe 大量资源——先按字段名/口径定位候选，再 describe 确认。该指引文案 MUST 保持中性、不点名任何具体工具。

#### Scenario: 绑定 MCP 时注入资源映射指引

- **WHEN** agent 绑定至少一个 MCP
- **THEN** 系统提示包含资源映射指引
- **AND** 指引不硬编码任何具体工具名

### Requirement: 预算临近提示须包含落盘保留语义

预算临近软提示 MUST 提醒模型优先落盘中间产物再收尾，并明确已落盘内容在断点续跑时保留。触发时机不变（剩余轮次为 5 或 2），且 MUST NOT 引入硬门禁。

#### Scenario: 预算临近时提示落盘保留

- **WHEN** 剩余轮次为 5 或 2
- **THEN** 注入预算告急提示，内容含「落盘中间产物」与「已落盘内容断点续跑保留」语义
- **AND** 不产生硬性中断
