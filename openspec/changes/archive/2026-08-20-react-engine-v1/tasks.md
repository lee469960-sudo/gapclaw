## 1. 软提示聚合 + 可观测基座

- [x] 1.1 `context_manager.py` 新增 hint 聚合:单迭代收集多条活跃 hint → 带短标签 join → 一次 `push_coach_hint`,无一条被覆盖
- [x] 1.2 `McpSessionManager.close()` 吞掉单会话关闭异常,不遮蔽主循环原始异常
- [x] 1.3 会话复用 / 失效重建 / 去重命中 均产生可见步骤或日志记录
- [x] 1.4 测试:同一迭代多提示并发时无提示丢失;取消/异常路径 close 触发且不残留子进程

## 2. FINAL 语义

- [x] 2.1 `runtime.py` FINAL 分支内计算 `skipped = [非 PLAN 非 FINAL 工具步骤]`,非空时经聚合层注入可见步骤 + 软提示
- [x] 2.2 测试:`SHELL`+`FINAL` 同轮产生告警且 SHELL 不执行;仅 `FINAL` 无额外告警;`PLAN`+`FINAL` 不触发非 PLAN 告警

## 3. MCP 执行层(会话复用 + 失效恢复 + 多 MCP 分派)

- [x] 3.1 `mcp_client.py` 新增 `McpSessionManager`:按 `mcp.id` 缓存 stdio/streamable 会话,`call_tool` 复用或建会话,`close` 关闭全部;legacy http 直调不缓存
- [x] 3.2 会话失效恢复:stdio 子进程退出、streamable 连接/session 错误 → 丢旧会话重建 → 该次调用重试一次,分层于 transport 重试
- [x] 3.3 `agent_tools.execute_action` 增加可选 `mcp_sessions` 参数,非 None 走 `call_tool`,None 回退 `call_mcp_tool`
- [x] 3.4 `runtime._run_modular` 用 `async with McpSessionManager()` 包住主循环体,`execute_action(..., mcp_sessions=sessions)`
- [x] 3.5 多 MCP 分派:遍历绑定 MCP 按工具名匹配(复用 `_get_mcp_tools_cached`),不再固定返回第一个;无命中返回「未在绑定 MCP 中找到工具 X」
- [x] 3.6 测试:同一 MCP 多次调用只 initialize/spawn 一次;运行结束会话关闭;失效重试一次;多 MCP 分派到实际声明该工具的 MCP

## 4. 续跑层(checkpoint 扩展 + 续跑注入 + 查询去重 + Verifier 增强)

- [x] 4.1 `_save_run_state` payload 追加 `run_ts`/`mcp_results`/`query_cache`/`plan_text`
- [x] 4.2 `_load_run_state` 恢复四字段(`.get` 缺省空),续跑 `mcp_result_seq` 从 `len(mcp_results)` 恢复
- [x] 4.3 `run_ts` 有则复用无则新生成;`_apply_plan` 持久化原始 `plan_text`
- [x] 4.4 续跑 `set_task_context(goal, plan=渲染子任务清单 + plan_text)`,并回填 `mcp_results` path + `saved_paths` 进 progress_block
- [x] 4.5 查询去重:执行前算 `key=(mcp_id, tool, 规范化 args)`,命中返引用;未命中落盘记账;in-run 内存 + cross-run checkpoint 两层生命周期 + LRU/大小上限
- [x] 4.6 软子任务提示:`_apply_plan`/进度更新时 coach hint 提醒重发带 `[x]` 的完整 PLAN(经聚合层)
- [x] 4.7 `_reflect_final` prompt 追加渲染后子任务清单 + 最近 progress
- [x] 4.8 测试:扩展 `test_run_state_roundtrip`(四字段保真);新增 `test_resume_injects_context`、`test_query_cache_dedup`

## 5. SOP 口径迁出引擎

- [x] 5.1 确认 `ads-sync-hub/references/` 覆盖范围,决定报表 SOP 并入 `sync-sop.md` 还是新增 `report-sop.md`
- [x] 5.2 把 `build_tools_desc` 中 ads SOP/报表四要素/pandas-xlsx/依赖自装迁到 Skill references,与既有 `sync-sop.md` 去重合并
- [x] 5.3 删除 `build_tools_desc`/`build_minimal_tools_desc` 硬编码 SOP 与 `utils._MCP_TOOL_EXAMPLES`/`_format_mcp_tools_for_prompt` 的 ads 硬规则;目录改为中性「工具名 + 描述 + required」并标注来源 MCP
- [x] 5.4 多 MCP 分派指引写入 Skill references(按工具归属 MCP 选 SOP)
- [x] 5.5 更新受影响测试断言:引擎目录含 `list_ads_views` → 中性目录不含 ads 硬编码

## 6. 同步 docs/react-engine.md

- [x] 6.1 重写为 v1 架构:删已合并/删除模块与废弃硬门禁表、中文 FINAL 别名,补记 `_reflect_final`/`_distill_final`/`_rescue_leaked_code`/native function-calling/断点续跑/会话复用/查询去重/软提示聚合

## 7. 冗余清理 + 验证收尾

- [x] 7.1 扫描并删除本次改动后失效的 helper(保留 MCP 测试端点仍用的 `call_mcp_tool` 路径)
- [x] 7.2 运行 `apps/api` 下 agent_runtime 相关测试 + 全量 108 测试,确认全绿、无新增静态门禁/阈值
- [x] 7.3 运行 `openspec validate --strict react-engine-v1`
