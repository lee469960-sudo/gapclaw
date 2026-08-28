# Design — react-engine-v8

## Context

动机与根因见 `proposal.md`「Why」与 `docs/exploration/react-engine-v8.md`。铁律贯穿：

- **铁律**：无任何静态硬门禁。V8 三条需求全部是「动作权限语义修复 + 补齐普通工具动作」，不引入任何循环级计数器 / 阈值 / 硬停。
- 路由现状：`_is_conversational`（`runtime.py:266-272`）只判 `mcp_ids/rag_ids/skill_ids`；`_run_conversational`（单轮无工具）与 `_run_modular`（工具循环）两条路径。
- 权限现状：`allowed_actions` 是单一门禁，`_run_modular` 已正确读取（`runtime.py:1870`）并门控 shell（`system_prompt.py:254-270`、`agent_tools.py:75-84`）。
- HttpMcp 现状：独立资源（`models.py:520`），`/pages/page_httpmcp.cgi` 提供 list/create/test/call，与 agent 零关联；`_tools()` 返回多 tool（name/url/method/description/args），`call_httpmcp(hm, vars)` 按 `vars.tool` 选工具。
- 文件动作现状：`file_read`/`file_write`/`file_search_replace` 全部作用在 host 侧 `workplace_root(sandbox.id)`（`agent_tools.py:312-352`），无任何内容 grep 能力。

## Goals / Non-Goals

**Goals:**
- shell-only agent 能真正执行 shell（路由修复）。
- `httpmcp_call` 成为受 `allowed_actions` 门控的、可调用绑定 HttpMcp 的真实动作。
- `file_search` 成为受 `allowed_actions` 门控的、递归 workplace 的内容 grep 动作。

**Non-Goals:**
- 不改 `HttpMcp` 实体 / `page_httpmcp.cgi`（独立页面保留原样）。
- `file_search` 不搜沙箱容器内其它路径（仅 workplace，与 READ 一致）。
- 不动 `mcp_tool_call`/`file_read`/`file_write`/`file_search_replace` 现有行为。

## Decisions

### D1: 路由修复 = 最小语义修复（方案 A）

`_is_conversational` 扩展为「无资源绑定 **且** `allowed_actions` 无任何工具动作」才走对话路径；否则走 `_run_modular`。不动 `_run_modular`/`_run_conversational` 本体。
- **为何**：根因只在路由，执行层已正确；最小改动、最低回归面。

### D2: httpmcp 绑定 = 新增 `httpmcps` 字段

`Agent` 加 `httpmcps` JSON Text（默认 `"[]"`）+ `to_dict` 透出 + 前端选择器，镜像 `mcps`/`rags`/`skills`。
- **为何**：MCP/RAG/Skill 三资源均走 per-agent 绑定，HttpMcp 保持一致；隔离清晰、上下文可控。

### D3: httpmcp 协议 = 镜像 MCP 按工具名

`HTTPMCP: <工具名> {json vars}`（+ 原生 `httpmcp_call(tool_name, arguments)`），把绑定 HttpMcp 的 `_tools()` 展平为目录（name+desc+args），按工具名解析（跨实体同名「第一个命中」，与 MCP 一致）。
- **为何**：复用 `mcp_tool_call` 的成熟解析/门控路径，模型心智一致。

### D4: file_search = 内容 grep

`SEARCH: <query>`（+ 原生 `file_search(query)`）递归 `workplace_root`，子串匹配（大小写不敏感），返回 `file:line: 片段`，cap N 条 + 总长度截断；跳过二进制/隐藏目录。
- **为何**：`file_read` 读单文件/列目录、`file_search_replace` 精准编辑，均非「跨文件内容搜索」，这是独立能力。

### D5: 两动作全链路白名单一致

`DEFAULT_ACTIONS`/`KNOWN_ACTIONS` → `models.py` 默认值 → `system_prompt.py`（提示词 + `build_tool_schemas`）→ `tool_parser.py` → `llm_client.py` → `agent_tools.py` → `context_manager.py` → `runtime.py`（auto-append + 传参）。任一环漏配即复现「勾选没生效」。
- **为何**：本次 bug 的教训——前端 key 与后端全链路必须同源，不能只改白名单一处。

### D6: 铁律不变

两个新动作都是普通工具动作，无任何静态硬门禁 / 阈值。

## Risks / Trade-offs

- [风险] 路由修复后默认 agent（`allowed_actions` 含 shell）从对话路径改走工具循环，行为面变宽。→ 缓解：这是 bug 的正确修复；工具循环本就支持纯文本回复，纯对话 agent（无工具动作）仍走对话路径。
- [风险] `httpmcp_call` 展平目录可能混入多个 HttpMcp 的同名工具。→ 缓解：与 MCP 同策略（绑定顺序第一个命中）；`{json vars}` 可传 `tool` 字段指定。
- [风险] `file_search` 大目录递归可能慢。→ 缓解：cap N 条 + 总长度截断；跳过二进制/隐藏目录。
- [风险] `HttpMcp` 调用超时/异常。→ 缓解：`call_httpmcp` 已有 timeout（默认 300s）；异常落到现有 step 错误通道。

## Open Questions

- `file_search` 匹配是否要支持正则（当前定子串）；命中片段上下文行数。
- `httpmcp_call` 结果是否需与 MCP 一样做大结果落盘（`task/<ts>/mcp_result_*.json`）。
