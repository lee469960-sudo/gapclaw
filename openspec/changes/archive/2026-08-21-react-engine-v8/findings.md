# Findings — react-engine-v8（执行中记录）

记录执行过程中发现的问题、张力与决策。与 `progress.md` 互补：本文件记录「问题/决策」，`progress.md` 记录「完成事实」。

## F1: httpmcps 绑定的后端 plumbing 未在 tasks.md 单列（R2/前端）

- **发现**：tasks.md 只写了 `models.py`（2.1 字段）、`agent.py`（2.2 白名单）、`Agents.vue`（4.1 选择器）。但「选择器 + 表单字段」要端到端可用，还需三处 plumbing：`AgentBody.httpmcps` 字段、`_agent_form_refs` 返回 `httpmcps` 列表、`agent_post` 持久化 `a.httpmcps`、以及 `startup.py` 为存量库 `agents` 加 `httpmcps` 列。
- **决策**：按 design D2「镜像 mcps/rags/skills」补齐上述 plumbing（未新增任务，属 2.1/4.1 的隐含落点），并在 `startup.py` 加 `ALTER TABLE agents ADD COLUMN httpmcps TEXT DEFAULT '[]'`（沿用既有迁移模式）。

## F2: 前端动作勾选项早已存在，「死勾选项」根因纯在后端（R2/R3）

- **发现**：`Agents.vue` 的 `ACTION_GROUPS` 早已包含 `httpmcp_call`（HTTP 请求代理）与 `file_search`（搜索文件内容），且 `ALL_TOGGLEABLE_ACTIONS`/`KNOWN_ACTION_KEYS` 也认它们。真正的 bug 在后端：`agent.py` 的 `DEFAULT_ACTIONS`/`KNOWN_ACTIONS` 缺这两个 key，`_normalize_allowed_actions` 把它们静默丢弃。
- **决策**：修复只动后端（`DEFAULT_ACTIONS` 加两个 key），前端 `ACTION_GROUPS` 无需改动。这也解释了 proposal「后端全库 grep -w 均 0 命中」。

## F3: httpmcp 目录展平只进文本目录，不进原生 schema（R2）

- **发现**：`build_tool_schemas` 只声明固定 meta 工具集，`httpmcp_call` 的 `arguments` 是泛化 object，不逐个展开各 HttpMcp 工具的 args；逐工具的 name+desc+args 展平只能放文本目录 `build_tools_desc`。
- **决策**：与 MCP 一致（v7 F1）——`build_tool_schemas` 加 `httpmcp_call`（`tool_name`+`arguments`），文本目录 `build_tools_desc` 展平 `_tools()`（name+desc+args，cap 30）。

## F4: `_search_workplace` 复用既有文件动作地基（R3）

- **发现**：`workplace_root`/`_sandbox_id`/`BINARY_EXTS` 已由 `file_read`/`file_write`/`file_search_replace` 使用，内容 grep 可直接复用。
- **决策**：`_search_workplace` 递归 `workplace_root`，子串匹配（大小写不敏感），cap 200 条 / 6000 字符总长；跳过隐藏目录（dot 前缀 / node_modules / `.git` 等）与二进制（`BINARY_EXTS` 扩展名 + 前 4KB 含 NUL 字节）。

## F5: `httpmcp_call` 不入 models.py 默认值（R2/R3 差异）

- **发现**：tasks 3.1 只要求 models.py `allowed_actions` 默认值加 `file_search`，未要求加 `httpmcp_call`。
- **决策**：保持如此——`httpmcp_call` 是资源绑定型动作，绑定 httpmcp 时由 `run_agent` auto-append（镜像 `mcp_tool_call`），无需进默认值；`file_search` 是非资源工具动作，须进默认值，否则新 agent 默认缺该动作。
