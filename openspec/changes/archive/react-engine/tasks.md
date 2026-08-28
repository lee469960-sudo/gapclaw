## 1. FINAL + 工具同轮告警(防护)

- [ ] 1.1 在 `runtime.py::_run_modular` 的 FINAL 分支内,计算被跳过的非 PLAN 工具步骤(`skipped = [s for s in tool_steps if s.action != "plan" and not s.is_final]`),非空时注入 `cm.push_coach_hint`,陈述「本轮 FINAL 与工具同时出现,工具未执行」
- [ ] 1.2 追加一条可见执行步骤(`_append_step`,action=`replan` 或 `skipped_tools`),标题/内容指明被跳过的工具名,status 可见
- [ ] 1.3 新增测试:同一轮 `SHELL: …` + `FINAL: …` 产生告警步骤且 SHELL 不执行;仅 `FINAL` 不产生额外告警;`PLAN` + `FINAL` 不触发非 PLAN 告警(对齐 `specs/agent-runtime/spec.md` 三个场景)

## 2. SOP 口径迁出引擎

- [ ] 2.1 确认 `ads-sync-hub/references/` 现有覆盖范围,决定报表 SOP(四要素/pandas/xlsx)并入 `sync-sop.md` 还是新增 `report-sop.md`(见 design Open Questions)
- [ ] 2.2 把 `system_prompt.py::build_tools_desc` 中「导出/查询策略」「报表 FINAL 四要素」「xlsx 生成示例」「依赖自装」迁到 Skill references,与已有 `sync-sop.md` 去重合并
- [ ] 2.3 从 `build_tools_desc`/`build_minimal_tools_desc` 删除硬编码 ads SOP,替换为中性引导(如「详细导出 SOP 见已绑定 Skill 的 references」)
- [ ] 2.4 清理 `utils.py::_MCP_TOOL_EXAMPLES` 中 ads 专用条目与 `_format_mcp_tools_for_prompt` 的 ads 硬规则分支;判定 `list_notes`/`recall` 是否业务口径,按结论迁移或保留
- [ ] 2.5 更新受影响测试(`test_mcp_catalog` 等),把「引擎目录含 `list_ads_views`」的断言改为「中性目录不含 ads 硬编码」

## 3. 同步 docs/react-engine.md

- [ ] 3.1 重写 §1.2 模块表:删除 `decision_engine.py`/`tool_router.py`/`tool_executor.py`/`intent_types.py`,并入 `tool_parser.py` + `agent_tools.py`
- [ ] 3.2 重写 §5:硬门禁表 → 软提示(教练提示 + 完成度复核器),FINAL 别名收窄为 `FINAL|Final Answer`
- [ ] 3.3 补记新增机制:`_reflect_final`、`_distill_final`、`_rescue_leaked_code`、native function-calling、`AgentRunState` 断点续跑
- [ ] 3.4 补记本 change 新增的「FINAL+工具同轮告警」行为与 SOP 归属(业务口径只写 Skill)

## 4. 验证与收尾

- [ ] 4.1 运行 `apps/api` 下 agent_runtime 相关测试(`test_final_reflection.py`、`test_single_loop_e2e.py`、`test_mcp_catalog.py`、`test_tool_parser.py` 等)
- [ ] 4.2 运行 `openspec validate --strict react-engine`
- [ ] 4.3 复查存量 agent 的 skill/mcp 绑定组合,评估 SOP 迁出对「绑定 MCP 但未绑定 ads-sync-hub Skill」agent 的影响面
