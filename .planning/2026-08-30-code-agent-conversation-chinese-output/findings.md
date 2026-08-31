# Findings: code-agent-conversation-chinese-output

- `apps/api/app/services/code_agent/claude_code_runtime.py` 在生成 Claude Code 命令时未限定用户可读摘要语言，因此模型可能返回英文摘要。
- `apps/api/app/services/code_agent/results.py` 的 `format_patch_verification_output` 同时包含中文与英文展示标签。
- 运行事件中的命令、文件路径、错误标识和 JSON 证据属于审计数据；本 change 只中文化用户可读文案，不翻译这些原文。
- Task 2.1 首次结果测试失败仅因旧断言仍匹配 `Patch 验证结果` 标题；已改为新中文标题后重新验证，非运行时缺陷。
