# Findings — react-engine-v11（执行中记录）

记录执行过程中发现的问题、张力与决策。与 `progress.md` 互补：本文件记录「问题/决策」，`progress.md` 记录「完成事实」。

## F1 — 自引用与「组套组」校验顺序（task 1.1）

spec 为「成员自引用」与「成员为组（组套组）」各立一个场景。但一个自引用成员 `mid == 组自身 id` 时，该成员**既是自引用、又是 group 成员**（组自身的 type 是 `group`），两条校验同时命中。若先查 `member.type != "llm"` 再查 `mid == item.id`，自引用会被误报成「组套组」。

落点：**先判 `mid == item.id`（自引用）**，再判「不存在」、再判「非叶子」，保证自引用返回更具体的「自引用」文案，符合 spec 两个场景各自的措辞。

## F2 — 环错误用 RuntimeError 子类而非裸 RuntimeError（task 2.3）

tasks.md 2.3 写「`raise RuntimeError("模型组存在循环引用或嵌套过深")`」。若用裸 `RuntimeError`，group 循环里的 `except Exception as e: last_err = e; continue` 会把环错误吞掉、再包一层「模型组全部失败:」，导致消息变成「模型组全部失败: 模型组存在循环引用或嵌套过深」——仍是单层，但措辞误导（"全部失败" 实为环）。

落点：新增 `LLMGroupCycleError(RuntimeError)` 子类，group 循环里 `except LLMGroupCycleError: raise`（先于 `except Exception`），使环 / 超深错误**直接上抛、不被叠加前缀**。仍是 RuntimeError 子类，2.3 的「raise RuntimeError(...)」契约成立，且满足 spec「不产生上千层嵌套错误消息」与「不嵌套叠加前缀」。

## F3 — `test_llm_chat` 环错误用返回值而非抛异常（task 2.2）

`test_llm_chat` 是字符串返回接口（失败返回 `"测试失败: ..."`，不抛异常，见 `routers/llm.py:113` 的 `test` 动作直接回显返回值）。环检测在 `test_llm_chat` 里落为 `return "模型组存在循环引用或嵌套过深"`（非 `"测试失败"` 前缀），使父组循环的 `if not result.startswith("测试失败")` 能把它当作「已解析出结果」直接上抛，而非继续试下一个成员。与 `chat_completion` 的抛异常形态等价，只是适配了该函数的返回契约。
