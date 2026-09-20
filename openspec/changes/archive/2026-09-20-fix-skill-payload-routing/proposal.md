## Why

Skill 包生成请求中的风险规则示例被当成真实操作，且分类器与人工等待判断使用不同文本，导致进入 React Engine 前误报 `human_wait`。之前只测试局部函数，未覆盖运行入口。

## What Changes

- 统一分类器与人工等待的风险判断，区分当前指令和待生成正文。
- 不把 Skill/文件协议或正文示例识别为未绑定 MCP；已绑定 Skill 名称参与路由。
- 删除生成 Skill 请求无条件绕过人工确认的豁免，保留真实操作和显式人工确认信号。
- 增加完整请求及 Agent 入口的回归验证。

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `conversation-execution-policy`: 当前指令与生成正文分离，统一路由与人工确认判断。

## Impact

影响 execution_policy 与标准 Agent 运行入口；不改变工具权限、MCP 连接或数据库结构。回滚通过恢复此前提交；保留普通对话快速路径。
