## 1. Helpers and loop state

- [x] 1.1 增加 `_subtasks_ready_for_final` / `_open_subtask_texts` / `_effective_fix_list`；验证套话条目被丢弃、开口子任务被列出
- [x] 1.2 `AgentLoopState.fix_only_until_final` 写入 `_save_run_state` / `_load_run_state`；验证续跑恢复该标志

## 2. `_run_modular` 完成度路径

- [x] 2.1 存在未完成具名子任务时丢掉 FINAL，不调用 `_reflect_final`、不增加失败计数；验证同轮其它工具仍执行
- [x] 2.2 空 `fix_list` 或全套话 FAIL 视为 PASS，接受候选
- [x] 2.3 有效 FAIL：不 `_apply_plan(revised)`；步骤标题为「请按修复清单执行」；只回灌有效 `fix_list`；置修复期
- [x] 2.4 修复期内丢弃 PLAN；真实工具成功（非缓存）将 `reflect_fail_count` 清零；连续 3 次有效 FAIL 仍收敛

## 3. 测试与回归

- [x] 3.1 新增 `tests/test_react_engine_v18.py`：证据门、空话 PASS、FAIL 后 PLAN 不落地、工具成功后需再 3 次 FAIL 才收敛、checkpoint 恢复 `fix_only`
- [x] 3.2 更新 `test_react_engine_v5.py` 收敛夹具子任务为 `done`；PLAN+FINAL 夹具在要收尾的场景使用 `[x]`
- [x] 3.3 跑 `python -m pytest tests/test_react_engine_v18.py tests/test_react_engine_v5.py tests/test_plan_only_nudge.py tests/test_final_semantics.py tests/test_final_reflection.py -q` 全绿
