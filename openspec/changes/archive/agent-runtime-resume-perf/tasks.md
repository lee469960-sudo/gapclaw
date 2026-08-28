## 1. MCP 会话复用 + 失效恢复 + 多 MCP 分派

- [ ] 1.1 在 `mcp_client.py` 新增 `McpSessionManager`:按 `mcp.id` 缓存 stdio/streamable 会话,`call_tool(mcp, tool, args)` 复用或建会话,`close()` 关闭全部会话与 stdio 子进程;legacy http 直调不缓存
- [ ] 1.2 会话失效恢复:stdio 检测子进程已退出(`proc.returncode` 非空)、streamable 遇连接/session 错误时,丢旧会话 → 重建(重新 `initialize`/spawn)→ 该次调用重试一次,分层于现有 transport 重试之上
- [ ] 1.3 `agent_tools.execute_action` 增加可选 `mcp_sessions` 参数:非 None 走 `mcp_sessions.call_tool`,None 回退现有 `call_mcp_tool`(MCP 测试端点路径零影响)
- [ ] 1.4 `runtime._run_modular` 用 `async with McpSessionManager() as sessions:` 包住主循环体,`execute_action(..., mcp_sessions=sessions)`;确保取消/异常/预算耗尽等所有提前返回路径都触发 close
- [ ] 1.5 多 MCP 分派:`execute_action` 的 MCP 分支改为按工具名匹配(复用 `_get_mcp_tools_cached` 的目录),不再固定 `return` 第一个绑定的 MCP;无命中时返回「未在绑定 MCP 中找到工具 X」
- [ ] 1.6 测试:同一 MCP 多次调用只 initialize/spawn 一次;运行结束会话关闭(含异常/取消路径);会话失效重试一次;多 MCP 分派到实际声明该工具的 MCP

## 2. checkpoint 扩展 + 续跑上下文注入

- [ ] 2.1 `_save_run_state` payload 追加 `run_ts`、`mcp_results`(`[{seq, path, tool, args, size}]`)、`query_cache`(`{key: {path, tool, size}}`)、`plan_text`
- [ ] 2.2 `_load_run_state` 恢复这四个字段(用 `.get` 缺省空,向后兼容旧 checkpoint);续跑时 `mcp_result_seq` 从 `len(mcp_results)` 恢复
- [ ] 2.3 `run_ts` 生成:加载的 checkpoint 含 `run_ts` 则复用,否则 `str(int(time.time()*1000))`;`_apply_plan` 把原始 `plan_text` 一并持久化
- [ ] 2.4 续跑时 `cm.set_task_context(goal=..., plan=_render_subtask_list(state.subtasks) + plan_text)`,并把 `mcp_results` 的 path 与 `saved_paths` 回填 progress_block
- [ ] 2.5 扩展 `test_run_state_roundtrip`(四字段 roundtrip 保真);新增 `test_resume_injects_context`(续跑重建 state,且 task_context 含渲染后子任务清单 + plan_text)

## 3. 查询去重 + 软子任务提示 + Verifier 输入增强

- [ ] 3.1 `mcp_tool_call` 执行前算 `key = (mcp_id, tool_name, 规范化 args)`,命中 `query_cache` 直接返回「已缓存,结果见 <path>」引用(不重调、不落新文件、不重复 push tool_result)
- [ ] 3.2 未命中照常调用,大结果落盘后记入 `mcp_results` + `query_cache`;LRU 上限按条数丢最旧(仅内存卫生,非循环硬门禁)
- [ ] 3.3 软子任务提示:`_apply_plan` 或进度更新时 coach hint 提醒「完成子任务后重发带 [x] 的完整 PLAN」,无推断/计数
- [ ] 3.4 `_reflect_final` prompt 追加渲染后子任务清单 + 最近 progress,使复核按子任务完成度判定
- [ ] 3.5 新增 `test_query_cache_dedup`(相同查询只 call_mcp_tool 一次、只一个 `mcp_result_N.json`、不重复 push tool_result)

## 4. 冗余代码清理 + 验证收尾

- [ ] 4.1 扫描并删除本次改动后失效的 helper(保留仍被 MCP 测试端点使用的 `call_mcp_tool` 路径)
- [ ] 4.2 运行 `apps/api` 下 agent_runtime 相关测试与全量 108 测试,确认全绿、长任务循环无新增静态门禁/阈值
- [ ] 4.3 运行 `openspec validate --strict agent-runtime-resume-perf`
