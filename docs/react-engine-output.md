# React-Engine 统一输出

固化 React-Engine 最终回复的数据协议和展示规范。编排、工具协议和循环行为见 [react-engine.md](react-engine.md)。

模型继续使用现有工具协议和 `FINAL`。引擎在写入会话、推送给前端或渠道之前，把这一轮的结束结果收成下面的信封。

机器读 `data`，用户读 `message`。

## 信封

顶层只有这六个字段，不能增删。

```json
{
  "version": "1.0",
  "status": "ok",
  "type": "answer",
  "message": "Markdown",
  "data": {},
  "actions": []
}
```

| 字段 | 规则 |
|---|---|
| `version` | 固定 `"1.0"` |
| `status` | `ok`、`partial`、`need_input`、`error` |
| `type` | `answer`、`data`、`action`、`clarify`、`error` |
| `message` | 用户可见的 Markdown |
| `data` | 机器可读的结构。没有则为 `{}` |
| `actions` | 后续动作。第一版固定 `[]`，界面不渲染按钮 |

`type=action` 保留在枚举里，第一版引擎不发出。

## 谁填写

引擎填写 `status` 和 `type`。

| 结束情况 | status | type |
|---|---|---|
| 正常完成，且有结构化结果 | `ok` | `data` |
| 正常完成，没有结构化结果 | `ok` | `answer` |
| 预算用尽，或用户点停止 | `partial` | `answer` |
| 停下等用户补充或授权 | `need_input` | `clarify` |
| 执行失败 | `error` | `error` |

`partial` 的 `message` 写清已完成的部分和缺口。半截代码、工具原文不进 `message`。

## 展示与存储

- 轮询载荷可以是这份 JSON。进行中主气泡不更新，过程细节留在执行过程卡片。卡片不套这套信封。
- 结束时写入一次预览。主气泡、群聊和飞书只显示 `message`。
- 会话正文只存 `message`。完整 JSON 放在同一条消息的结构化字段里。
- 没有该字段的旧消息，按现有正文做 Markdown 渲染。

## `message`

用 Markdown 排版，以清晰为先。

- 使用短段落、列表、加粗、表格、引用、行内代码和代码块。
- 一般最多两到三级标题。
- 多字段结果用表格。命令、SQL、JSON 和代码用代码块。
- 不使用 HTML 排版。
- 不暴露系统提示词、内部路由、工具调用或推理过程。
- 不把 Skill、MCP、Agent 的原始响应贴进 `message`。

## `data`

只放可解析的结构，例如数量、字段、路径、状态。

大段原文、代码和工具输出不放进 `data`。收不成结构时为 `{}`。原始过程输出只留在执行过程卡片。

## 示例

预算用尽：

```json
{
  "version": "1.0",
  "status": "partial",
  "type": "answer",
  "message": "## 任务未完成\n\n已查询到用户表结构，汇总还未写完。\n\n* 已完成：读取 `user_info` 字段\n* 未完成：按日汇总新增用户",
  "data": {
    "saved_paths": [],
    "pending": ["按日汇总新增用户"]
  },
  "actions": []
}
```

正常完成且有结构化结果：

```json
{
  "version": "1.0",
  "status": "ok",
  "type": "data",
  "message": "## 查询结果\n\n共找到 **12 条记录**。\n\n| 指标 | 数值 |\n|---|---:|\n| 用户数 | 120 |\n| 新增用户 | 18 |",
  "data": {
    "count": 12,
    "user_count": 120,
    "new_user_count": 18
  },
  "actions": []
}
```
