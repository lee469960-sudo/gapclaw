## 1. llm_client — ChatResult 与空响应软路径

- [x] 1.1 扩展 `ChatResult` 增加 `output_truncated: bool = False`；验证字段可被 runtime 读取
- [x] 1.2 `extract_chat_response_text`：空 content / 无可执行 tool_calls 时返回 `""`，不再 raise「缺少 content」；验证纯空与 reasoning-only 均不抛错
- [x] 1.3 `_build_chat_result`：只保留可映射的 tool_calls；不可映射或 `finish_reason=length` 的 tool_calls 清空且不执行；验证单测覆盖

## 2. llm_client — 输出预算与请求级管道

- [x] 2.1 `fit_messages_to_context` 增加 `min_allowed_out`；重试路径传入 ≥2048 时尽量抬高 `allowed_out`；验证 `min_allowed_out=2048` 时返回值 ≥2048（窗口允许时）
- [x] 2.2 `chat_completion` 叶子管道：无可执行产出（非纯 thinking）→ 抬预算重试 1 次 → 仍空返回软空 `ChatResult`/`""`；验证不 raise、不计入组 failover
- [x] 2.3 `finish_reason=length|max_tokens` 且有文本 → 同请求最多续写 2 次并拼接；成功 stop 返回全文；验证拼接与调用次数
- [x] 2.4 续写用尽仍 length → 返回拼接文本且 `output_truncated=True`；验证标记
- [x] 2.5 无 length 类 finish_reason 时不启发式续写；验证完整短答只打 1 次请求
- [x] 2.6 模型组：成员空 200 软成功不换下一成员；真 4xx/5xx 仍 failover；验证组空不出现「模型组全部失败: LLM 响应缺少 content」

## 3. runtime — 截断阻断 FINAL

- [x] 3.1 `_run_modular` 在 FINAL / 完成信号处理前检查 `output_truncated`；为真则 coach「输出截断」后 `continue`，不进 `_reflect_final`；验证含 `FINAL:` 的截断残篇不会结束任务
- [x] 3.2 下一轮完整 FINAL 仍可正常结束；验证 reflect 仅在非截断轮触发

## 4. 测试与回归

- [x] 4.1 新增 `tests/test_react_engine_v17.py`：空 200 软空回、length 续写、仍截断标记、残缺 tool_calls 不执行、组不因空互踢、runtime 阻 FINAL
- [x] 4.2 跑 `python -m pytest tests/test_react_engine_v17.py tests/test_llm_native_tools.py tests/test_react_engine_v4.py tests/test_react_engine_v11.py -q` 全绿
- [x] 4.3 确认铁律：无新增循环硬门；MiniMax reasoning_split 仍开启；真错误 llm_failures 行为不变
