## ADDED Requirements

### Requirement: 内容生成请求区分外层指令和正文

系统 MUST 在风险判断和资源绑定检查中区分外层指令与 fenced payload；明确 Skill 内容生成请求的 Markdown 正文 MUST 被视为待生成内容。分类器和运行入口 MUST 使用一致的人工等待判断，不得因为正文示例误报人工等待。显式人工确认元数据及外层真实高风险操作 MUST 保持有效。

#### Scenario: 生成风险规则 Skill

- **WHEN** 用户要求制作 Skill 包并提供含生产发布、权限修改等示例的 fenced 或 Markdown 正文
- **THEN** 系统进入任务模式并到达 React Engine
- **AND** 正文风险示例、协议词和环境词不触发人工等待或未绑定 MCP 错误

#### Scenario: 生成后执行风险操作

- **WHEN** 用户要求创建 Skill 并实际发布到生产环境，或明确设置人工确认元数据
- **THEN** 系统保留人工等待，不因提及创建 Skill 而豁免

#### Scenario: 已绑定 Skill 可用于执行

- **WHEN** 用户点名已绑定 Skill，且允许读取 Skill 和写文件
- **THEN** 请求进入任务模式，Skill 内容能够注入 modular runner 并执行读取和文件写入

#### Scenario: 隐含选股请求命中绑定市场数据 MCP

- **WHEN** 用户提出“选股、筛选、过滤”等外部行情任务，且条件词匹配当前 Agent 已绑定 Tushare 或其他市场数据 MCP 的名称、标签或描述
- **THEN** 系统进入任务模式并执行 MCP 工具调用
- **AND** 仅解释“什么是涨停”等概念的问题保持普通对话

#### Scenario: 能力元数据语义匹配进入任务模式

- **WHEN** 用户消息未点名 MCP，但包含至少两个与已绑定 MCP 名称、标签或描述匹配的能力词，并且不是定义/解释类知识问题
- **THEN** 系统进入任务模式
- **AND** 不需要为每个 MCP 增加固定关键词

#### Scenario: 绑定 MCP 任务不得在首轮无工具调用时完成

- **WHEN** 已绑定并选中的 MCP 任务首轮仅返回文字 FINAL，未产生任何 MCP 工具调用
- **THEN** React Engine MUST 记录纠偏事件并继续一轮推理，要求调用匹配的 MCP 工具
- **AND** 该纠偏 MUST 有界，不能将普通对话或后续补充路由变成无限重试

#### Scenario: 稀疏 MCP 元数据下的结构化外部任务

- **WHEN** Agent 已绑定能力描述较短的 MCP，用户使用“选出/筛选 + 条件列表、日期或排除条件”提出外部数据任务
- **THEN** 请求 MUST 进入任务模式，不得因为条件词未逐字出现在 MCP 标签中而走 chat 快速通道
- **AND** 具体 MCP 仍由能力路由模型根据绑定候选选择，不增加 MCP 专属名称硬编码
