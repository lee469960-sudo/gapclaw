## 1. 非 FINAL 退出前持久化最新运行状态

- [x] 1.1 `runtime.py` 预算耗尽分支（`max_iters` 且存在未完成子任务）`return` 前调用 `_save_run_state(ctx, state)`，去掉「≥2 子任务」限制
- [x] 1.2 `runtime.py` LLM 连续失败分支 `return` 前持久化最新状态
- [x] 1.3 续跑回填覆盖非 FINAL 退出后的 `mcp_results` / `query_cache` / `progress_lines`（复用现有恢复逻辑，验证「已缓存 MCP 结果」回填与去重生效）

## 2. 原生 tool_calls 全程配对

- [x] 2.1 权限阻止分支（`action not in allowed_actions`）对原生 step 回填合成 `role:tool` 结果（「已阻止未启用工具」）
- [x] 2.2 FINAL 同轮跳过分支对原生 step 回填合成 `role:tool` 结果（「FINAL 优先，未执行」）
- [x] 2.3 验证下一轮消息中每条 assistant `tool_calls` 均有配对 tool 消息

## 3. 动态软提示与进度块不被截断丢弃

- [x] 3.1 `llm_client.py` `fit_messages_to_context` 合并 system 层时，将 `coach_hint` / `progress_block` 排除在头截断之外
- [x] 3.2 超限时优先裁 `tools_catalog` / `skill_snapshot` 静态层

## 4. 完成度复核连续拒绝后收敛

- [x] 4.1 `runtime.py` 按 run 计数连续 `_reflect_final` FAIL，达到阈值（3）后接受候选或强制 `_distill_final` 收尾
- [x] 4.2 收敛时记录并呈现被拒原因（供用户观察），不空转到 `max_iters`

## 5. 复核拒绝时回灌修复清单

- [x] 5.1 FAIL 分支把 `fix_list`（或修订 PLAN）随「完成度反思」coach hint 一并注入

## 6. mcp_results 有界

- [x] 6.1 `mcp_client.py` / persist 侧对 `mcp_results` 设上限（保留最近 N 条，镜像 `query_cache` LRU）

## 7. 原生 role:tool 结果 in-loop 裁剪

- [x] 7.1 `context_manager.py` `trim_tool_results` 覆盖原生 history 的 assistant+tool 原子组（复用原子组删除逻辑）

## 8. 文案与死分支清理

- [x] 8.1 失败退出文案匹配真实持久化语义（落盘则「已保留」、未落盘则「已暂停」）
- [x] 8.2 删除预算耗尽处 `if state.final:` 死分支

## 9. 测试与回归

- [x] 9.1 新增「非 FINAL 退出持久化 + 续跑恢复」单测（预算耗尽 / LLM 连续失败两路径）
- [x] 9.2 新增「原生 tool_calls 被阻止 / 跳过时也回填合成结果」单测
- [x] 9.3 新增「动态层不被截断丢弃」单测（大目录下 coach_hint / progress_block 完整保留）
- [x] 9.4 新增「完成度复核连续拒绝收敛 + fix_list 回灌」单测
- [x] 9.5 新增 `mcp_results` 有界 + 原生 in-loop 裁剪单测
- [x] 9.6 全量测试回归绿；无新增静态门禁 / 硬中断
