## 1. agent-runtime — 完成信号软转换

- [x] 1.1 `runtime.py` 文本无工具分支前：正则粗筛正向完成声明（关键词 + 非疑问 + 排除「无法/不能/吗/？」）
- [x] 1.2 `loop_state.py` 新增完成信号疑似计数；连续 2 轮疑似时用一次 LLM 确认「是否完成声明」
- [x] 1.3 确认后把文本当 FINAL 候选送入 `_reflect_final`；FAIL 走定向补；任何进度/复核重置计数
- [x] 1.4 负向「无法完成/无法继续/无法连接」不触发；仍走现有纯文本提示路径

## 2. agent-runtime — 需求感知完成度复核

- [x] 2.1 `_reflect_final` prompt：现场按 goal 逐条拆核对项，逐项判 PASS/FAIL
- [x] 2.2 去掉「小瑕疵一律 PASS」；改为「字段口径/数字/映射必须精确，仅排版措辞豁免」
- [x] 2.3 FAIL 时只定向补缺失项，保留已完成子任务 `[x]` 与已写文件，不全量覆盖 plan

## 3. agent-runtime — 已完成清单注入

- [x] 3.1 `context_manager.py` task_context 稳定层每轮回显：子任务勾选 + saved_paths + progress 后 N 条 + 已尝试工具摘要
- [x] 3.2 resume 时额外注入「上次执行到此、还差 X」
- [x] 3.3 清单引擎零 LLM 拼（progress_lines / saved_paths / subtasks / tool_call_tally / query_cache）
- [x] 3.4 `trim_tool_results` 豁免清单，清单永不裁

## 4. agent-runtime — MCP 工具调用去重 + 大结果取回指引

- [x] 4.1 所有 MCP 工具按「工具名+归一化参数」进 query_cache；`execute_ads_sql` 按 SQL 归一化（去空白/大小写/尾分号；LIMIT/OFFSET 不同视为不同）
- [x] 4.2 命中回显「该结果已缓存/已落盘 path，请 READ 取回，勿重跑」
- [x] 4.3 大结果落盘后回显完整取回路径（而非只回显截断片段）

## 5. 测试与回归

- [x] 5.1 完成信号软转换单测（正向声明 2 轮 → LLM 确认 → 当 FINAL；否定/疑问不触发）
- [x] 5.2 需求感知复核单测（渠道名称未映射判 FAIL；FAIL 只定向补、完成态保留）
- [x] 5.3 已完成清单单测（每轮回显；裁剪后清单仍在；resume 注入）
- [x] 5.4 MCP 去重单测（相同工具+归一化参数命中；execute_ads_sql 相同 SQL 归一化命中；LIMIT/OFFSET 不同不去重；大结果落盘回显路径）
- [x] 5.5 全量回归绿；确认无新增确定性硬停
