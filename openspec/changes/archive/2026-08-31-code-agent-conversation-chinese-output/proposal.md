## Why

CodeAgent 的平台阶段标题大多已经中文化，但 Claude Code 面向用户的任务摘要仍可能以英文返回，Patch 验证结果也混用英文展示标签。这使中文使用者难以连续阅读同一轮任务的执行过程与最终结论。

## What Changes

- 要求 Claude Code 将面向用户的任务摘要、澄清和失败说明以简体中文输出。
- 将 Patch 验证结果的用户可读标签统一为中文。
- 保留命令、文件路径、错误码、状态标识和折叠的原始证据，避免翻译改变可审计性或排障信息。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `code-agent-conversation-progress`: CodeAgent 对话运行摘要与最终结果的用户可读展示语言要求调整为简体中文。

## Impact

- `apps/api/app/services/code_agent/claude_code_runtime.py` 的 Claude Code 提示词。
- `apps/api/app/services/code_agent/results.py` 的最终 Markdown 输出。
- 对应后端结果和运行时测试。
