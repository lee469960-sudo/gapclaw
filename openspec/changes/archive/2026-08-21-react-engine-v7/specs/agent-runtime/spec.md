## ADDED Requirements

### Requirement: MCP 分页参数透出与同轮补齐引导

当 agent 绑定 MCP 时，引擎 MUST 在工具目录中透出 MCP 工具 `inputSchema` 的分页参数（offset/limit/page/page_size 等，含非 required 字段），使模型知道能分页；系统提示 MUST 引导模型「结果若被截断（返回行数 ≈ 所设 limit），同一轮用 offset 补齐剩余页」。引擎 MUST 不新增自动翻页逻辑——分页由模型自主发起，引擎仅按既有能力同轮串行执行多条 `MCP:`。

#### Scenario: 透出分页参数

- **WHEN** agent 绑定至少一个 MCP
- **THEN** MCP 工具目录包含其 `inputSchema` 的分页参数（offset/limit/page/page_size 等，含非 required 字段）
- **AND** 模型能在提示词中看到这些参数

#### Scenario: 同轮补齐引导

- **WHEN** 系统提示构建且 agent 绑定 MCP
- **THEN** 提示包含「结果被截断时同轮用 offset 补齐剩余页」的引导

#### Scenario: 不新增引擎自动翻页

- **WHEN** MCP 查询返回被截断的结果
- **THEN** 引擎不自动翻页、不注入 offset
- **AND** 分页完全由模型在提示词下自主发出多条 `MCP:`，引擎同轮串行执行（既有能力）

### Requirement: 交付附件标注引导

当 agent 可能产出需作为附件发送的文件时，系统提示 MUST 引导模型在 `FINAL:` 中标注 `attach=<path1,path2>`（或独立 `ATTACH:` 行）。该标注 MUST 仅为渠道层识别交付文件的约定，不影响 `FINAL:` 的其它语义；未标注 MUST 不报错。

#### Scenario: 引导标注附件

- **WHEN** 系统提示构建
- **THEN** 提示包含「产出文件需附件发送时，在 `FINAL:` 标注 `attach=<path>`（或独立 `ATTACH:` 行）」的格式说明

#### Scenario: 未标注不强制

- **WHEN** 模型在 `FINAL:` 未标注 `attach`
- **THEN** 引擎不报错，交由渠道兜底逻辑处理

### Requirement: 查询效率提示词强化

当 agent 绑定 MCP 时，系统提示 MUST 新增独立【查询效率】段，显式写入「优先用 WHERE/LIMIT 收窄、避免全表扫描、聚合（COUNT/SUM 等）在 SQL 侧完成、按总等待时间选最优查询」。该强化 MUST 仅为提示词软引导，不新增方案评估 / 成本模型等结构性机制。

#### Scenario: 独立查询效率段

- **WHEN** agent 绑定至少一个 MCP
- **THEN** 系统提示包含独立【查询效率】段，含 WHERE/LIMIT、避免全表扫描、聚合优先、按总等待时间选最优查询的显式引导

#### Scenario: 无结构性机制

- **WHEN** 模型选择查询方案
- **THEN** 引擎不评分、不选最优、不拦截，仅由提示词软引导
