## Context

当前 CodeAgent 已有 run-bound Workspace、Claude Code stream-json 事件和对话执行过程卡片。归档后的修复需要补齐两个边界：Workspace 文件内容不能再以原始纯文本呈现，Claude 协议初始化记录不能进入摘要；同时入口层不应把 Claude Code 启动绑定到特定自然语言前缀。

## Goals / Non-Goals

**Goals:**

- 复用现有 Markdown 预览与代码块工具栏，在 Code Workspace 面板中渲染文件内容。
- 通过组件级事件委托处理复制，并提供 Clipboard API 失败回退。
- 保持 CodeAgent 的现有 Run、Workspace、Sandbox、Verifier 和 Artifact 边界，让普通对话直接进入已绑定的 Claude Code runtime。
- 在服务端和历史消息展示层过滤 Claude stream-json 协议元数据。

**Non-Goals:**

- 不改变 Repository 获取、Sandbox 创建、Verifier、Sealer 或 Git 提交/推送权限。
- 不引入新的 LLM 类型、模型路由或第二套代码执行引擎。
- 不把 Workspace Markdown 预览变成可编辑器；文件修改仍由 CodeAgent runtime 完成。

## Decisions

1. **按文件类型选择渲染模式。** `.md/.mdx` 进入现有 Markdown 管线；其他文本文件包装为带语言标识的 fenced code block。这样既保留文档结构，也避免普通源码被误解析成 Markdown。
2. **复制使用事件委托。** `v-html` 生成的工具栏按钮不参与 Vue 模板编译，因此在预览容器上监听点击并定位 `.md-code-action[data-code-action=copy]`；标准 Clipboard API 失败时使用隐藏 textarea 的兼容路径。
3. **协议过滤放在摘要解析和历史展示两层。** 服务端不再把 `system/init` 记录作为 runtime summary；前端对历史截断残片做兼容过滤，避免旧消息继续泄漏初始化 JSON。
4. **入口默认直接创建 Run。** 对 Claude CodeAgent 的非空普通消息直接作为 objective；旧“开始执行:”只做可选兼容剥离。这样不修改下游 CodeAgent runtime 契约，也不会产生无 Run 的 grill 占位轮次。

## Risks / Trade-offs

- [Risk] Markdown 文件可能包含原始 HTML。→ 预览沿用现有 Markdown 管线和前端 HTML 渲染约定；Workspace 访问仍受当前 Run 授权和路径边界保护。
- [Risk] 旧浏览器拒绝剪贴板写入。→ 使用 `execCommand('copy')` 回退，并显示失败提示。
- [Risk] 历史消息中的协议 JSON 可能已被截断。→ 前端按 `system/init` 前缀兼容过滤，新消息则由服务端在摘要阶段阻断。
- [Risk] 普通消息现在会创建真实 Code Run，可能增加运行次数。→ 仅对已绑定 Claude Code runtime 的 CodeAgent 生效，空消息仍拒绝；运行预算和现有停止操作保持不变。

## Migration Plan

1. 部署 API 和前端变更。
2. 重启 API/Runner，确保新的路由和摘要解析加载。
3. 强制刷新浏览器，验证既有历史消息、Markdown 文件预览和复制按钮。
4. 发送一条不带前缀的 CodeAgent 任务，确认创建 Code Run、展示过程并生成终态结果。
5. 回滚时恢复上一版前端/API；不涉及数据库 schema 迁移。
