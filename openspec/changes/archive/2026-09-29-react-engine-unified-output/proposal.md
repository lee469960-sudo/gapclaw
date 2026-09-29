## Why

任务未完成时，React-Engine 常把中间产物（代码、工具原文、JSON）写进用户可见回复，会话和渠道上看起来像一段未完成的代码。已确认的输出契约要求轮询使用统一 JSON，用户最终只看 Markdown 预览。现在需要把该契约落到运行时，而不是只留在文档里。

## What Changes

- 引擎在写入会话和向外推送之前，把一次运行的结束结果收成固定六字段信封：`version`、`status`、`type`、`message`、`data`、`actions`。
- 会话正文、主气泡、群聊和 IM 渠道只保存并展示 `message`（Markdown）。完整信封放在同一条消息的结构化字段。
- 进行中主气泡不更新；执行过程卡片保持现有步骤结构。
- 预算用尽和用户停止使用 `status=partial`；必须等用户补充或授权时使用 `need_input`；执行失败使用 `error`。
- `data` 只保存可解析结构。原始代码和工具输出不进入 `message` 或 `data`。
- `actions` 第一版固定为 `[]`，界面不渲染按钮。`type=action` 第一版不发出。
- 没有信封字段的旧消息继续按现有正文做 Markdown 渲染。

## Capabilities

### New Capabilities

- `react-engine-output`: React-Engine 最终回复的统一信封、Markdown 展示、状态分类和存储边界。

### Modified Capabilities

- `agent-runtime`: 运行结束时的用户可见回复必须经过统一信封收口。用户取消和预算耗尽的可见结果改为 `partial` 说明，不再把半截中间产物或单独的「已停止」当作最终回复。

## Impact

- `apps/api/app/services/agent_runtime/runtime.py`：结束路径（FINAL、预算耗尽、用户停止、失败、等待用户）在落库前归一化。
- 会话消息模型与聊天 API：正文与信封字段分离。
- Web 会话与群聊：主气泡只渲染 `message`；进行中不把轮询 JSON 刷进主气泡。
- IM 渠道：继续发送正文 Markdown，不发送原始信封。
- 契约文档 `docs/react-engine-output.md` 已记录目标行为；本变更负责实现与回归。
- 不改变工具协议、`FINAL` 语法、执行过程卡片的步骤结构，也不新增操作按钮。
