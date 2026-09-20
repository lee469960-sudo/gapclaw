## Context

当前 `classify_request` 直接扫描原文，`requires_human_wait` 使用去掉代码块的文本。前者返回人工等待后，后者返回 false 不会撤销结果。Skill 和文件协议还曾误入 MCP 绑定校验。

## Goals / Non-Goals

- Goal: 内容生成请求能够到达 React Engine，真实风险操作及显式人工确认继续暂停。
- Non-goal: 自动授予写文件权限、强制模型调用工具、调整部署或 Agent 数据。

## Decisions

1. 共用当前指令提取和风险判断：去掉 fenced payload；明确生成 Skill 且包含 Markdown 正文标题时，将正文与外层指令分离。没有正文边界的混合实际操作请求不豁免。
2. `classify_request` 调用 `requires_human_wait`，不直接使用风险正则；元数据人工确认优先。
3. 绑定检查使用相同外层指令，排除协议和环境词，并认可绑定 Skill 名称。
4. 验证完整入口而非只验证帮助函数：请求到达 modular runner，执行 Skill 读取与文件写入；真实风险请求不调用 LLM。

## Risks / Trade-offs

正文边界仅用于明确 Skill 生成及 fenced payload，不推断任意语句含义。真实工具仍受已有权限检查保护。无状态迁移及 MCP 重试变化。
