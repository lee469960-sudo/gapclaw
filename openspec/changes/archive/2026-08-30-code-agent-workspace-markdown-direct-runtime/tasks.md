## 1. Workspace Markdown 预览与复制

- [x] 1.1 为 CodeWorkspacePanel 增加 Markdown 文件渲染和常见代码/配置文件语言代码块预览，并通过前端构建验证
- [x] 1.2 接入代码块复制按钮的事件委托、Clipboard API 回退和成功/失败提示，并通过前端构建验证

## 2. Claude Runtime 事件与结果

- [x] 2.1 从 Claude Edit/Write/工具结果提取脱敏、限长的阶段代码片段，并在实时/历史执行步骤中展示；通过 Claude Runtime 与结果测试验证
- [x] 2.2 过滤 stream-json 的 system/init 协议记录，避免原始 JSON 进入摘要或历史消息，并通过 Runtime/控制面回归验证

## 3. CodeAgent 对话路由

- [x] 3.1 让绑定 Claude Code runtime 的 CodeAgent 普通非空消息直接创建 Code Run，保留“开始执行:”可选兼容并拒绝空目标；通过 coding SOP/控制面测试验证
- [x] 3.2 移除 CodeAgent 对话中强制 grill/“开始实现”触发逻辑，确认现有 Run、Workspace、Verifier 和提交策略不变

## 4. 验收

- [x] 4.1 运行后端相关回归测试、Python 编译检查和 git diff 检查
- [x] 4.2 运行前端生产构建并确认 Markdown 预览与复制按钮无编译错误
