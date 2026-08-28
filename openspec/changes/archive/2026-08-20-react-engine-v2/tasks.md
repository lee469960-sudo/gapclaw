## 1. 运行时软提示（Q2）

- [x] 1.1 `runtime.py::_run_modular` 的 `if not tool_steps:` 分支内、`else:`（`_rescue_leaked_code` 判定之后、`text_only_streak += 1` 之前）新增 `if plan_steps:` 注入「规划待执行」软提示（文案按 design D3 中性化，不写工具名/前缀）
- [x] 1.2 确认 FINAL 分支在 `if not tool_steps:` 之前 return/continue，PLAN+FINAL 同轮无需额外守卫（仅核对，不改动）

## 2. 系统提示解耦（Q3）

- [x] 2.1 `system_prompt.py`「规划·子任务」条目追加一句：子任务文本只写目标、不写工具名（含正例 `- [ ] 找到白名单视图` 与反例 `- [ ] list_ads_views → 找白名单视图`）

## 3. 测试（Q4）

- [x] 3.1 新增单测：PLAN-only 一轮后必注入「规划待执行」hint（断言下一轮 LLM 上下文含该提示，或 ContextManager 缓冲含该提示）
- [x] 3.2 新增单测：PLAN+工具同轮不发该 hint
- [x] 3.3 新增边界用例：PLAN+FINAL 同轮不发该 hint
- [x] 3.4 全量测试回归绿（`python -m pytest tests/ -q`），无新增静态门禁/阈值/计数器
