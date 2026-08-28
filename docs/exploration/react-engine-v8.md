# react-engine-v8 — 动作权限修复 + 补齐 httpmcp_call / file_search

## Context

用户报告两个「勾选没生效」类 bug，根因均已坐实：

1. **shell 误路由**：「Linux 运维工程师」agent 配置了 shell 权限，却在沙箱中不能执行命令，返回的是**对话回复**而非 `[permission_denied]`。
2. **死勾选项**：前端 `Agents.vue` 暴露 7 个动作勾选项，其中 `httpmcp_call`（HTTP 请求代理）、`file_search`（搜索文件内容）两个在后端**没有任何对应动作**，勾了被 `_normalize_allowed_actions` 静默丢弃。

铁律贯穿：**无任何静态硬门禁**。本 V8 全部改动为**动作权限语义修复** + **补齐两个普通工具动作**，不新增循环级阈值 / 计数器 / 硬停。

## Root Causes

### Bug 1 — `_is_conversational` 忽略 `allowed_actions`

`runtime.py:266-272` 的 `_is_conversational` 只看 `ctx.mcp_ids / ctx.rag_ids / ctx.skill_ids`，**完全忽略 `allowed_actions`**。于是 shell-only agent（无 MCP/RAG/skill 绑定，但 `allowed_actions` 含 `shell`）被误判为「无工具对话」，走 `_run_conversational`（单轮、无工具），而 `build_conversational_system`（`system_prompt.py:87-101`）明确禁止 `SHELL:/PLAN:/FINAL:`。所以 shell 勾了等于没勾。

`_run_modular` 工具循环本身**已正确**读取 `allowed_actions`（`runtime.py:1870`）并门控 shell（`system_prompt.py:254-270`、`agent_tools.py:75-84`）——问题只在**路由**，不在执行。

### Bug 2 — 前后端动作 key 漂移

| 前端勾选项 | 后端是否存在该 action | 证据 |
|---|---|---|
| `httpmcp_call` | ❌ | 后端全库 `grep -w httpmcp_call` 0 命中。HTTP MCP 是独立老页面 `/pages/page_httpmcp.cgi`（`routers/httpmcp.py` + `httpmcp_runner.call_httpmcp`），**不是 agent ReAct 动作** |
| `file_search` | ❌ | 后端全库 `grep -w file_search` 0 命中，只有 `file_search_replace`（PATCH，另一个勾选项） |

后端 `_normalize_allowed_actions`（`agent.py:63-67`）把 `KNOWN_ACTIONS` 之外的 key 静默丢弃。其余 5 个勾选项后端均有完整实现，无此问题。

**用户决策**：这两个勾选项**做成真实动作**（不是移除）。

## Requirements

### R1 — `_is_conversational` 语义修复（Bug 1）

`_is_conversational` 的判定必须从「仅资源绑定」扩展为「资源绑定 **且** 无任何工具动作」。判定为对话路径当且仅当：

- 无 MCP / RAG / skill / httpmcp 资源绑定（新增 `httpmcps` 后一并纳入），**且**
- `allowed_actions` 中不含任何**非资源绑定**的工具动作：`shell` / `file_read` / `file_write` / `file_search_replace` / `file_search` / `recall`。

（`mcp_tool_call` / `rag_query` / `skill_read_md` / `skill_run_script` / `httpmcp_call` 属资源绑定动作，已由各自 id 判定覆盖，无需重复进集合。）

**行为影响**：默认 agent（`allowed_actions` 含 shell）改走 `_run_modular` 工具循环，shell 恢复正常；真正无工具的纯对话 agent 仍走 `_run_conversational`。

### R2 — `httpmcp_call` 真实动作（Bug 2a）

把 `HttpMcp` 资源接进 agent 的 ReAct 循环，作为一个受 `allowed_actions` 门控的工具动作：

- **绑定**：`Agent` 新增 `httpmcps` JSON Text 字段（默认 `"[]"`）+ `to_dict` 透出；`Agents.vue` 加 HttpMcp 选择器（镜像 mcps/rags/skills picker）。
- **协议**（镜像 `mcp_tool_call`）：文本 `HTTPMCP: <工具名> {json vars}`；原生 schema `httpmcp_call(tool_name, arguments)`。把绑定 HttpMcp 的 `_tools()` 展平为目录（name + desc + args），按工具名解析到对应 HttpMcp。
- **执行**：`execute_action` 新增 `httpmcp_call` 分支，接收 `httpmcp_ids`，按工具名定位 → `call_httpmcp(hm, {**vars, "tool": tool_name})`；无绑定返回 `no http mcp configured`，找不到工具名返回提示。
- **门控**：`DEFAULT_ACTIONS` / `KNOWN_ACTIONS` 加入 `httpmcp_call`；绑定 httpmcp 时 auto-append（镜像 `runtime.py:1875` 的 mcp_tool_call 逻辑）。

### R3 — `file_search` 真实动作（Bug 2b）

新增内容 grep 动作（区别于 `file_read` 读文件 / `file_search_replace` 精准编辑）：

- **语义**：递归 `workplace_root(sandbox.id)`，对文本文件做子串匹配（大小写不敏感），返回 `file:line: 片段`，cap N 条 + 总长度；跳过二进制 / 隐藏目录。
- **协议**：文本 `SEARCH: <query>`；原生 schema `file_search(query)`。
- **执行**：`execute_action` 新增 `file_search` 分支 → `_search_workplace(sandbox, query)`（host 侧，与 READ 一致）。
- **门控**：`DEFAULT_ACTIONS` / `KNOWN_ACTIONS` / `models.py` 默认值加入 `file_search`。

## Decisions

- **D1**：Bug 1 修法 = 方案 A（最小语义修复），改 `_is_conversational` 判定，不动 `_run_modular` / `_run_conversational` 本体。
- **D2**：`httpmcp_call` 绑定 = 新增 `httpmcps` 字段 + 选择器（镜像 mcps/rags/skills），不列全量。
- **D3**：`httpmcp_call` 协议 = 镜像 MCP，`HTTPMCP: <工具名> {json vars}`，按工具名展平解析（跨实体同名按「第一个命中」解析，与 MCP 一致）。
- **D4**：`file_search` 语义 = 内容 grep，`SEARCH: <query>`，递归 workplace，子串匹配，cap N。
- **D5**：两个新动作**全链路白名单一致**——`DEFAULT_ACTIONS`/`KNOWN_ACTIONS`（`agent.py`）→ `models.py` 默认值 → `system_prompt.py`（提示词行 + `build_tool_schemas`）→ `tool_parser.py`（文本 regex）→ `llm_client.py`（原生 schema）→ `agent_tools.execute_action` → `context_manager.py`（结果标签）→ `runtime.py`（auto-append + `httpmcp_ids` 传入）。任一环漏配即复现「勾选没生效」。
- **D6**：铁律不变——两个新动作都是普通工具动作，无任何静态硬门禁 / 阈值。

## 全链路改动清单（OpenSpec 任务映射）

| 层 | R1 | R2 httpmcp_call | R3 file_search |
|---|---|---|---|
| 模型 `models.py` | — | `Agent.httpmcps` + `to_dict` | `allowed_actions` 默认值加 `file_search` |
| 白名单 `agent.py` | — | DEFAULT_ACTIONS + KNOWN_ACTIONS 加 `httpmcp_call` | DEFAULT_ACTIONS + KNOWN_ACTIONS 加 `file_search` |
| 路由 `runtime.py` | `_is_conversational` 判定 | 加载 `httpmcps`→`httpmcp_ids`、auto-append、传参 | — |
| 提示词 `system_prompt.py` | — | `HTTPMCP:` 目录 + `httpmcp_call` schema | `SEARCH:` 行 + `file_search` schema |
| 文本解析 `tool_parser.py` | — | `HTTPMCP:` regex | `SEARCH:` regex |
| 原生解析 `llm_client.py` | — | `httpmcp_call` 分支 | `file_search` 分支 |
| 执行 `agent_tools.py` | — | `httpmcp_call` 分支 | `file_search` 分支 + `_search_workplace` |
| 结果标签 `context_manager.py` | — | `httpmcp_call` 标签 | `file_search` 标签 |
| 前端 `Agents.vue` | — | HttpMcp 选择器 + `httpmcps` 字段 | — |

## Non-Goals

- 不改 `HttpMcp` 实体 / `page_httpmcp.cgi`（独立页面保留原样）。
- `file_search` 只搜 workplace（host 侧，与 READ 一致），不搜沙箱容器内其它路径。
- 不动 `mcp_tool_call` / `file_read` / `file_write` / `file_search_replace` 现有行为。

## 验证要点

1. shell-only agent（无资源绑定、`allowed_actions` 含 shell）走工具循环，能真正执行 `SHELL:`，不再是对话回复。
2. `httpmcp_call` 勾选后：绑定 HttpMcp 的 tools 进目录 → `HTTPMCP: <名> {vars}` 调通；无绑定 / 无权限时返回降级提示而非静默失败。
3. `file_search` 勾选后：`SEARCH: <query>` 返回 `file:line: 片段`；无匹配返回空提示。
4. 回归：现有测试 + v6（channels/transport）/ v7（MCP 分页 / attach / 查询效率）需求不回归。
5. `_is_conversational` 单测：资源绑定 + 动作集合两维度组合，断言路由正确。
