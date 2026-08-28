# Findings — react-engine-v7（执行中记录）

记录执行过程中发现的问题、张力与决策。与 `progress.md` 互补：本文件记录「问题/决策」，`progress.md` 记录「完成事实」。

## F1: `build_tool_schemas` 不枚举 MCP 工具的 inputSchema（R1）

- **发现**：tasks 1.1 写「`_format_mcp_tools_for_prompt` / `build_tool_schemas` 透出分页参数」，但 `build_tool_schemas`（`system_prompt.py`）只声明固定 meta 工具集，`mcp_tool_call` 的 `arguments` 是泛化 `object`，**不**逐个展开各 MCP 工具的 `inputSchema`；逐工具的 schema 只在文本目录 `_format_mcp_tools_for_prompt`（`utils.py`）里呈现。
- **决策**：分页参数（offset/limit/page/page_size/cursor，含非 required）只在 `_format_mcp_tools_for_prompt` 透出，符合 design D2「工具目录」与 spec scenario「MCP 工具目录包含 inputSchema 的分页参数」。`build_tool_schemas` 不动。

## F2: R1/R3 提示词 gate 在 `mcp_reachable` 而非字面「绑定」（R1/R3）

- **发现**：spec 措辞「当 agent 绑定至少一个 MCP」，但既有 取数效率/资源映射/导出报表 均 gate 在 `mcp_reachable`（至少一个绑定 MCP 真正返回非空 tools）；MCP 绑定但不可达时提示词已含「请勿反复重试 MCP」，此时再注入查询优化引导自相矛盾。
- **决策**：【分页补齐】【查询效率】两段随既有分支置于 `if mcp_reachable:` 内，「绑定」按「实际可用 MCP」理解，与既有模式一致（同 v6 F2 的处理）。

## F3: attach 解析/规范化的落点（R4）

- **发现**：design D10 说「attach 解析放 channels/runtime.py」，但纯解析/规范化/去标注是可单测的纯函数。
- **决策**：纯函数 `parse_attach_paths` / `strip_attach_markers` / `resolve_attach_paths` 放 `reply.py`（与既有 `format_im_completion_reply` 同层），编排在 `channels/runtime.py::process_inbound`。防穿越复用 `download_path`（`_path_under_root` + `find_files` 回退），未另起炉灶。多附件逐个 `send_document` 复用既有 `push_channel_documents`（非 TG 的 base 抛 `NotImplementedError` → 既有 catch 兜住，行为不变）。

## F4: task 1.3 确认——无引擎自动翻页

- **确认**：`context_manager.py:90` 的 `push_coach_hint("try using offset=100")` 是类 docstring 的用法示例，非实际翻页逻辑；`runtime.py` / `mcp_client.py` 无 offset 注入、无续拉循环。分页完全由模型在提示词下自主发起。
