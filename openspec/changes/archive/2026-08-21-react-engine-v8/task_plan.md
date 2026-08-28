# Task Plan — react-engine-v8

> `tasks.md` 是唯一正式任务来源。本文件只做「任务 → 执行阶段」的映射与依赖排序，不新增、不改写、不删减任何需求或验收标准。

## 任务总量

- 23 个任务（tasks.md 分组 1–5）。
- 5 个执行阶段 A–E。

## 执行阶段映射

### 阶段 A — R1 对话路由尊重工具动作（tasks 1.1–1.2）

| Task | 内容 | 依赖 |
|------|------|------|
| 1.1 | `_is_conversational` 扩展判定 | 1.2（httpmcp 纳入资源绑定） |
| 1.2 | `runtime.py` 加载 `httpmcps` → `ctx.httpmcp_ids` | 2.1（需 `Agent.httpmcps` 字段先存在） |

**注意**：1.2 硬依赖 2.1（模型字段），故阶段 B 的 2.1 需先行落地；阶段 A 与阶段 B 首步（2.1）交织。

### 阶段 B — R2 httpmcp_call 真实动作（tasks 2.1–2.8）

| Task | 内容 | 依赖 |
|------|------|------|
| 2.1 | `models.py` `Agent.httpmcps` + `to_dict` 透出 | —（先行，供 1.2 / 4.1） |
| 2.2 | `agent.py` `DEFAULT_ACTIONS`/`KNOWN_ACTIONS` 加 `httpmcp_call` | — |
| 2.3 | `system_prompt.py` 展平目录 + `HTTPMCP:` 提示词 + schema | 2.1 |
| 2.4 | `tool_parser.py` `HTTPMCP:` regex | — |
| 2.5 | `llm_client.py` `httpmcp_call` 原生分支 | 2.4 |
| 2.6 | `agent_tools.py` `execute_action` `httpmcp_call` 分支 | 1.2（`httpmcp_ids`） |
| 2.7 | `runtime.py` auto-append + 传 `httpmcp_ids` | 1.2、2.6 |
| 2.8 | `context_manager.py` `httpmcp_call` 结果标签 | 2.6 |

### 阶段 C — R3 file_search 真实动作（tasks 3.1–3.7）

| Task | 内容 | 依赖 |
|------|------|------|
| 3.1 | `models.py` `allowed_actions` 默认值加 `file_search` | —（与 2.1 同文件，合并落地） |
| 3.2 | `agent.py` `DEFAULT_ACTIONS`/`KNOWN_ACTIONS` 加 `file_search` | —（与 2.2 同文件，合并落地） |
| 3.3 | `system_prompt.py` `SEARCH:` 提示词 + schema | — |
| 3.4 | `tool_parser.py` `SEARCH:` regex | — |
| 3.5 | `llm_client.py` `file_search` 原生分支 | 3.4 |
| 3.6 | `agent_tools.py` `file_search` 分支 + `_search_workplace` | — |
| 3.7 | `context_manager.py` `file_search` 结果标签 | 3.6 |

### 阶段 D — 前端（tasks 4.1）

| Task | 内容 | 依赖 |
|------|------|------|
| 4.1 | `Agents.vue` HttpMcp 选择器 + `httpmcps` 表单字段 | 2.1（字段透出） |

### 阶段 E — 测试与回归（tasks 5.1–5.5）

| Task | 内容 | 依赖 |
|------|------|------|
| 5.1 | `_is_conversational` 单测 | 1.1 |
| 5.2 | `HTTPMCP:` 解析 + 按工具名调用单测 | 2.x |
| 5.3 | `SEARCH:` 解析 + `_search_workplace` 单测 | 3.x |
| 5.4 | `build_tool_schemas` 含两动作断言 | 2.3、3.3 |
| 5.5 | 全量回归绿；无新增循环门禁 / 静态阈值 | 全部 |

## 依赖图

```
2.1 (models) ─┬─→ 1.2 ─→ 1.1 ─→ 5.1
              ├─→ 2.3 ─→ 2.4 ─→ 2.5 ─→ 2.6 ─→ 2.7/2.8 ─→ 5.2
              ├─→ 4.1 (前端)
              └─→ (阶段 C 与 B 并行，仅 models.py/agent.py 同文件合并编辑)
3.1/3.2 ─→ 3.3 ─→ 3.4 ─→ 3.5 ─→ 3.6 ─→ 3.7 ─→ 5.3
5.4 = 2.3 + 3.3 产物断言；5.5 = 全量回归
```

## 同文件合并提示（非需求，仅执行效率）

- `models.py`：2.1（httpmcps 字段）与 3.1（allowed_actions 默认值）同一文件，可一次编辑、分别勾选。
- `agent.py`：2.2 与 3.2 同文件同函数（`DEFAULT_ACTIONS`/`KNOWN_ACTIONS`），可一次编辑、分别勾选。
- `system_prompt.py` / `tool_parser.py` / `llm_client.py` / `agent_tools.py` / `context_manager.py`：R2 与 R3 各留独立分支，避免互相覆盖。

## 验收口径（来自 tasks.md，不重定义）

- 每个 task 勾选 = 对应代码落地 **且** 对应单测通过（阶段 E 覆盖），**且** 满足 specs/ 中 scenario 的 WHEN→THEN。
- 5.5 全量回归绿 + 无新增静态门禁是整体收口门槛。
