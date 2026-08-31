## 1. Claude Code 用户摘要中文化

- [x] 1.1 在 Claude Code Runtime 提示词中要求面向用户的任务摘要、澄清和失败说明使用简体中文，并通过 Runtime 命令构造测试验证该约束存在且不改变技术数据原文。

## 2. Patch 验证结果中文化

- [x] 2.1 将最终 Patch 验证 Markdown 的固定用户可读标题、字段标签和说明统一为简体中文，并通过结果格式化测试验证技术状态标识、命令、路径与证据仍保持原始值。

## 3. 验证与记录

- [x] 3.1 运行相关 Runtime 与结果测试、`git diff --check` 和 OpenSpec 严格校验，并将实际结果记录到 `progress.md`。
