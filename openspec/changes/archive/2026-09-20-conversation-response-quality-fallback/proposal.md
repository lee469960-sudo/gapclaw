## Why

普通对话快速路径目前只在 LLM 失败或返回空文本时兜底，复杂的总结、拆解、分析请求可能被当作 chat 并直接返回过短或缺少结论的单段文本。需要增加通用的复杂度识别、Agent 回复风格和一次有界的质量补全，同时保留简单闲聊的低延迟路径。

## What Changes

- 将总结、拆解、分析、梳理、对比、提取等复杂表达纳入通用请求复杂度判断。
- 增加 Agent 回复风格配置，支持自适应、简洁、结构化和分析型输出。
- 为普通对话增加有限的回复质量检查，识别过短、未覆盖关键实体、未完成用户要求等情况。
- 质量检查失败时最多执行一次针对性补全，不进入无限重试或完整 React Engine 循环。
- 对得到大脑等内容型 Agent，为来源、日期、主题和笔记梳理请求提供结构化回复策略。
- 在执行指标和会话步骤中记录路由、回复风格及质量补全结果。

## Capabilities

### New Capabilities

- `conversation-response-quality`: 普通对话复杂度识别、回复风格和有界质量补全。
- `agent-response-policy`: Agent 回复风格与质量策略配置。

### Modified Capabilities

- `conversation-execution-policy`: 扩展复杂对话路由与 chat 模式的有限质量重试语义。

## Impact

- 影响 `apps/api/app/services/agent_runtime/execution_policy.py`、`conversational.py`、`runtime.py`、Agent 配置模型和相关 API。
- 影响 Agent 编辑界面及会话执行详情中的路由/质量状态展示。
- 不增加工具权限，不改变高风险人工确认规则，不改变已选 MCP 的懒加载策略。
- 需要新增执行策略、普通对话、得到大脑场景和前端构建回归测试。
