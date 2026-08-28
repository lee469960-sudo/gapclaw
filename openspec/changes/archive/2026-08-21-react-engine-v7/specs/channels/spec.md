## ADDED Requirements

### Requirement: TG 附件发送

当 agent 的最终回复包含交付附件（`attach=` 标注解析出的路径，或兜底挑出的文件）且渠道为 TG 时，引擎 MUST 按路径经 `download_path` 规范化解析后调用 `send_document` 逐个发送；MUST 支持多附件；显式标注 MUST 优先于自动兜底。未标注 `attach` 时 MUST 保留现有「挑一个最新根目录 xlsx/csv 文件」的兜底逻辑。非 TG 渠道 MUST 行为不变。

#### Scenario: 显式标注多附件发送

- **WHEN** TG 回复包含 `attach=path1,path2`（或独立 `ATTACH:` 行）解析出的多个交付路径
- **THEN** 引擎按路径逐个 `send_document` 发出

#### Scenario: 显式优先 + 兜底

- **WHEN** 回复未标注 `attach`
- **THEN** 引擎保留现有「挑一个最新根目录 xlsx/csv 文件」逻辑作为兜底发送

#### Scenario: 非 TG 渠道行为不变

- **WHEN** 渠道非 TG（web 或其它未实现 `send_document` 的 IM）
- **THEN** 不发送附件，行为与现状一致
