# Findings: code-agent-workspace-conversation-ui

## Initial discoveries

- 通用 Agent 使用 `/workplace`，CodeAgent Runner 使用独立 `/workspace`。
- CodeAgent Run 已有 `workspace_path`、Runner facts、运行事件和结果序列化能力，可作为新 UI 的服务端事实来源。
- Workspace 预览必须按 `code_run_id` 授权和路径 containment，不能按 `sandbox_id` 回退到通用工作区。
- `dbt-clickhouse-gamestat` Skill 可能包含旧 `/workplace` 路径引用；CodeAgent 注入时已统一改写为 `/workspace`。

## Open issues

- 需要确认现有 Run 事件持久化字段和 AgentChat 事件订阅接口，避免重复实现事件存储。
- 需要确认前端左侧面板的现有组件边界，再决定新增 Code Workspace 组件还是条件切换现有面板。

## Execution findings

- 预览层与 Artifact/Verifier 已共享相同的 `.git`、`.claude` 隔离边界；运行时目录不会进入文件树、Git changed paths、Verifier 输入或 sealed patch。
- 旧客户端或旧服务端缺少 Code Workspace API 时，前端显示显式 `workspace_unsupported` 状态并保留对话结果；不会回退到 `/workplace` 冒充仓库。
- 并发 Run 验证表明 Workspace 与事件查询必须始终以 `code_run_id` 绑定，不能按 agent 或通用 sandbox 推断。
- 归档后回归发现：Run 进入 `retained_read_only` 时会移除 `.git`，因此 Git 元数据不可用不能阻断业务文件树预览；API 现已将其单独返回为 `git_metadata_missing`。
- 归档后回归发现：左侧树初版只渲染根层 entries，目录按钮没有展开行为；现已改为按需加载子目录，并用 Git changed paths 高亮修改文件。
- 归档后回归发现：Code Runtime 的 `runtime_started` 是成功确认事件，前端却将所有非 `completed` 状态都显示为运行中；`verify_baseline` 的 started/completed 事件也被重复追加，导致旧步骤永久转圈。现已按 Code 阶段复用步骤并将启动确认显示为完成。
- 归档后回归发现：`submit_chat` 先创建 pending Run 并异步执行仓库获取/Workspace 挂载；Code Workspace 面板收到 `code_run_id` 后的一次性请求可能早于挂载完成，误显示 `workspace_not_prepared`。现已将该状态视为临时态自动轮询，其他终态错误保持立即提示。
- 归档后回归发现：保留态 Workspace 会移除自身 `.git`；若 Git status 未先验证仓库根目录，Git 会向上查找宿主代码库并报告大量无关变更。Claude Code 的单顶层布局也不能直接用 snapshot index 比较；预览现优先按 Run baseline 计算变更，并拒绝父级 Git 根目录。
- 归档后回归发现：Code profile 过程原先只经 WebSocket 推送，刷新或事件早于 active Run 绑定时会丢失；同时实际 Run 的 Claude Code 失败原因为 `unrecognized_model`（MiniMax-M3），因此没有文件修改。现已持久化脱敏 profile 事件并增加 runtime_result 失败阶段，发送后主动回放。
- 继续回归发现：Claude Code 分支绕过 `AgentRuntime.run()`，原先既不发送共享 `done` 事件，也不把用户/助手结果落入聊天历史，导致前端一直转圈且刷新后过程消失。现已在 Claude Code 分支完成后发送终止事件、持久化用户与结果消息，并在前端单独回放 Runtime 历史步骤。
- 终止事件覆盖清理成功与清理失败两条路径；清理失败仍会先落盘失败结果并通知前端，避免任何异常路径永久显示运行中。
- 最新 Run 的首因仍是 Agent 绑定了 provider 为 OpenAI-compatible 的 MiniMax-M3。Claude Code 使用 Anthropic API，不能直接复用 MiniMax/OpenAI LLMResource；预检此前只检查“有 provider/model”，导致错误延迟到 SDK 的 `unrecognized_model`。现已在绑定校验阶段拒绝非 Anthropic provider，并给出替换 LLMResource 的修复指引。
- 按“只扩展现有 LLM、不增加新功能”的约束，复用现有 `LLMResource`/LLM 管理页面，增加 Anthropic Claude 配置预设和提示；不新增资源类型、网关或模型路由。MiniMax 继续保留给普通 Agent。
- 最新本地 Run 已通过 CLI/Workspace 预检，但实际 Claude API 返回 `401 API key is invalid`；因此仍没有进入 coding loop。错误此前被归类为 `coding_failed`，现已统一归类为 `model_unavailable`，避免误导为代码执行失败。
- 最新有效 Run 已进入 Claude 编码并产生 `gamestat/dbt_project.yml` 修改，但 Verifier 将其误判为 `workspace_external_write`：Claude 直接修改 Workspace，未经过平台 `code_edit` 登记。重试又因合成的 `code-agent-run-*` title 不是有效 Claude session 而失败。现已将受控 Claude 变更登记到 frozen `allowed_paths`，并对无效 resume session 自动以同一 Workspace 的新会话重试。
- 前端另有独立刷新断点：CodeAgent 侧栏组件未绑定 ref，运行完成时仅调用标准 Agent `WorkplacePanel.load()`；因此即使 run-bound Workspace 已被修改，树和文件预览仍停留在旧数据。现已新增 `codeWpRef` 并由统一刷新函数调用 Code Workspace 的公开 `load()`。
- `workspace_integrity_error` 是安全终态，不应进入 Verifier retry；否则会在完整性失败后继续修改 Workspace，并将 Claude CLI 的无效 `--resume` 错误叠加到原始原因上。运行时现已对该 outcome 立即停止，普通 `verification_failed` 仍按原 retry 策略处理。
- `CodeArtifactSealer` 会将 Run 标记为 `sealed`，而 cleanup hook 原先只保留 `prepared`，导致成功 Run 的 Workspace 被删除，左侧随后显示 `workspace_mount_invalid`。封存后现在保留只读目录；已删除的历史 sealed Run 则明确显示 `workspace_expired`，不再把生命周期清理误判成挂载错误。
- 实际数据库 Run `f513eb3a` 验证了该路径：`patch_ready + sealed + workspace_path 不存在`，并非 Claude 没有挂载或没有修改。该 Run 的变更事实已进入 `runner_facts`，artifact 可用于审阅；新部署后应重新执行任务以获得可浏览的 retained Workspace。
- Claude 返回“`/workspace/.claude/CLAUDE.md` doesn't exist”虽不影响单值替换，但暴露出注入时序风险：SOP 原先在 Runner 启动后写入。现改为 Workspace prepare 后、Runner start 前写入，保证容器启动可见；仍由现有 SOP 文件和 Skill Registry 提供内容，不新增框架。
- 第二次 CodeAgent 对话若只是澄清/拷问，`submit_chat` 合法返回空 `code_run_id`（grilling 状态）；前端原先先清空 `activeCodeRunId`，导致左侧错误退回“等待 CodeAgent Run”。现保留上一次 Run-bound Workspace，只有收到新 Run ID 才切换；会话切换时按当前 session 重新恢复最新 Run。
- 实际数据库中的成功 Run（例如 `patch_ready + sealed artifact`）会保留 `failure_reason=patch_ready` 作为终态标记；结果序列化若直接调用未知原因映射，会误显示 `infrastructure_error · Stage internal`。现对成功终态标记抑制失败映射，保留真实的 patch_ready/可采用状态。
- 同一结果序列化中的模型预检在 `passed=true` 时仍填充默认失败原因 `model_preflight_failed`，会让成功输出看起来矛盾；现仅在预检失败时填充失败原因。
- 刷新恢复链路确认：历史接口为减小消息体只返回 `step_count`，完整步骤需通过 `get_message_steps` 延迟获取；刷新后若不主动展开/拉取，用户会误以为执行过程丢失。CodeAgent 现对最新历史助手消息自动展开并调用该接口，运行中则继续从 `get_code_events` 回放。
- 实际 Run `7a4184aa` 的 `runtime_result: started` 长时间未结束，最后一个工具是 `pip3 install dbt-clickhouse...`；由于 sandbox 网络关闭且 Claude 可用 1800 秒/40 turns，依赖安装探测可能长时间阻塞。现向无网络 Claude 会话注入禁止下载依赖的提示，并设置 pip/npm/git 非交互超时；启动恢复还会追加失败终态事件，避免刷新后永久显示 started。
- 运行 `7a4184aa` 随后被服务重启恢复为 `infrastructure_error/startup_recovery`，但旧事件历史仍停在 `runtime_result: started`；仅修改 Janitor 不足以修复已存在 Run，因此 `get_code_events` 现在对已终止且缺少 Runtime 终态的历史动态补写失败事件。
- Claude CLI 当前通过现有 Runner 的同步命令执行，无法在子进程未返回前解析其 JSON 结果；因此执行中先显示稳定的 `runtime_result: started` 活动阶段，命令返回后再发布脱敏的具体 tool/test/file 事件和完成状态，避免伪造未收到的实时细节。
- LLM 管理页的测试请求此前将所有 provider 都按 OpenAI-compatible 处理，Anthropic 资源因此被错误发送到 `/chat/completions`，导致 `https://api.anthropic.com/chat/completions` 返回 404。现已为现有 `anthropic`/`cloud_claude` provider 增加原生 `/v1/messages` 请求分支，使用 `x-api-key`、`anthropic-version` 和 Anthropic response content 解析；未新增资源类型或模型路由。
- 同一 LLMResource 会向 Claude Code 注入 `ANTHROPIC_BASE_URL`；该值现复用 Anthropic 基地址规范化，清除历史配置中的 `/v1`、`/v1/messages` 或 `/chat/completions` 后缀，避免 CLI 再次重复拼接路径。
- UI 侧原先在对话上下文下方额外渲染 `Claude Code Runtime 过程` 历史卡片，与消息内的“执行过程”重复。现移除独立卡片和样式，profile 事件（包括历史回放）统一写入现有 live/message execution card；最终结果中的证据折叠区仍保留。
- 归档时 Claude Code 详细事件仍受同步 Runner 限制：`runtime_result: started` 在阻塞 CLI 前立即发布，但 `tool_call`、`file_changed`、`test_run` 等 Adapter 事件只有 CLI 返回后才能解析和回放；该限制已由下述流式通道消除。
- 已增加可选流式通道：Claude Code 使用 `--output-format stream-json --verbose`，Runner 通过 Docker exec stream 回调逐块交付 stdout；Adapter 将 Bash/Edit/Write 及工具结果转换为脱敏 profile 事件，并由运行时线程安全地推送现有 WebSocket 执行卡片。无回调时仍使用原 `--output-format json` 同步路径。
- 流式模式只改变 Claude Code 分支；Runner 的默认 `exec` 仍保持 `container.exec_run` 返回值路径。Docker stream 同时处理 stdout/stderr，并通过 exec inspect 获取退出码，避免改变普通 CodeTool 的协议。
- 实际部署出现 `RunnerUnavailableError: runner_exec_failed` 后确认，流式 exec 应传入已解析的 `container.id`（而不是外部字符串），并兼容旧 Docker daemon 拒绝 stream 协商的情况；Runner 现对 stream 建立阶段失败回退到同步 exec，避免任务直接失败（此回退不提供实时事件，但仍能得到最终结果）。
- 从最近 Run 的事件序列判断：Claude 已完成多个 Read/Grep/Edit/Bash 工具，失败更可能发生在流式 exec 收尾阶段，而不是 CLI 未启动。流式传输的观察者异常或 Docker 在 EOF 前重置连接会被旧实现包装成 `runner_exec_failed`。现将输出观察者隔离出命令主路径，并在流中断后以 `exec_inspect` 的退出码作为权威结果；仅无法取得退出码时才失败。
- Runner 失败结果现在保留脱敏的底层异常类型（不包含命令内容），便于区分容器查找、Docker stream 和事件发布问题。
- 新 Run `db980424` 仍能完成 CLI/Workspace/Skill 预检及多次工具调用，最终为 `OSError`；流式 exec 的 iterator 或其 `close()` 在 Docker socket 断开时均可能抛出该异常。`.close()` 现在被视为非关键清理失败，不覆盖已取得的 exec 结果。
- `db980424` 的失败发生在运行中的同一 Claude CLI 会话，而非启动预检；因此对 stream 断连后的 exec 状态等待从 1 秒扩展为最多 30 秒，给 Docker daemon 时间完成子进程收尾，同时仍受原始任务 deadline 限制。
- 用户确认每次执行需要看到 Patch 验证结果，且提交/推送由后续对话显式触发；结果输出已固定包含执行状态、Verifier、变更文件、可采用性和“未执行提交/推送”说明。CodeAgent 阶段行的详情改为同一行省略显示，错误仍保留独立详情。
- 首轮编码提示默认禁止 commit/push；当后续用户消息明确包含 `git commit`、`git push`、提交或推送时解除该提示限制，保证后续对话可以执行显式发布操作。
- 用户要求移除“Patch 已验证 / Code run …”独立结果 UI，并将其全部内容收敛到对话输出。前端已删除结果卡片及工件审阅弹窗；结果序列化层现在输出完整脱敏 Markdown，包含 Manifest、Runtime、Verifier、变更文件、可采用性、工件哈希、预检事实、证据 JSON 和主机验证命令。
- Claude Code 终态 `done` 事件不再只发送 500 字预览，而是发送完整已脱敏 Markdown；历史消息仍作为最终持久化来源，避免对话中显示不完整结果。
- 用户要求在 Patch 验证结果之前看到阶段代码片段。Claude 的流式工具事件原先只保留路径/命令，现从 Edit/Write 参数提取修改前后或写入内容，从工具结果提取有限输出，并统一 UTF-8 替换、脱敏和长度上限；前端使用原样 `<pre>` 渲染，避免 Markdown/HTML 二次解析造成乱码。
- 实际出现的长 JSON 是 Claude `stream-json` 的 `system/init` 协议事件；当没有可识别的最终 result 时，旧 `_runtime_summary` 将原始 JSON 当作摘要。现过滤 system/stream/user 协议记录，仅保留 assistant 文本或显式 result/error，并在历史 Code profile steps 中保留 snippet/output。
- 为兼容已持久化的旧消息，前端 `extractFinalDisplayContent` 额外过滤完整的 `system/init` JSON 行；普通 JSON 和代码块不受影响。
- 旧摘要可能已被 1000 字限制截断、不是合法 JSON；前端兼容按 `type=system/subtype=init` 前缀识别并过滤这类残片。
- CodeAgent Claude 路由原先把无前缀消息送入 grill，只有“开始执行:”或“开始实现”才创建 Run；现改为所有 Claude CodeAgent 消息直接创建 Code Run，前缀仅保留为兼容别名。
- 左侧 Code Workspace 预览原先用纯文本 `<pre>` 展示文件；现改为复用 Markdown 预览管线渲染文件内容，同时保留原文代码块和安全转义。
- 左侧预览的代码复制按钮由 Markdown 工具栏生成，但原组件没有事件委托；现接入点击处理并增加 Clipboard API 失败时的 `execCommand` 回退。
- 刷新后执行过程退化成「CodeAgent 运行阶段 · started/completed」：`_code_profile_steps_for_message` 用通用标题，且 `_slim_steps_for_meta` / `get_message_steps` 丢掉 snippet；历史卡片读的是这份精简数据，而不是实时用的 `get_code_events`。已改为刷新时按 profile 事件回放，并让新消息落库保留阶段标题与 snippet。
