## Purpose

为 CodeAgent 用户提供可读、可复制且不执行原始 HTML 的仓库文件预览，让 Markdown 文档和代码文件在 Workspace 面板中保持清晰的格式与编码。

## ADDED Requirements

### Requirement: Workspace 文件必须按内容类型美化预览

CodeAgent Workspace 文件预览 SHALL 对 Markdown 文档渲染标题、列表、表格和代码块；对常见代码与配置文件 SHALL 使用带语言标识的代码块展示，并保留原始文本内容。

#### Scenario: 预览 Markdown 文件

- **WHEN** 用户在 CodeAgent Workspace 中选择 `.md` 或 `.mdx` 文件
- **THEN** 面板展示渲染后的 Markdown 内容
- **AND** 中文、表格和代码块保持可读格式

#### Scenario: 预览代码或配置文件

- **WHEN** 用户选择 Python、SQL、JSON、YAML、JavaScript 或其他文本代码文件
- **THEN** 面板以对应语言代码块展示文件内容
- **AND** 不执行文件中的 HTML、脚本或命令

### Requirement: Workspace 代码块必须支持复制

Workspace 预览中的代码块 SHALL 提供复制操作；当标准 Clipboard API 不可用或权限被拒绝时，系统 SHALL 尝试浏览器兼容回退，并向用户反馈复制成功或失败。

#### Scenario: 复制代码成功

- **WHEN** 用户点击代码块的复制按钮且浏览器允许写入剪贴板
- **THEN** 系统将未高亮变形的代码文本写入剪贴板
- **AND** 显示“代码已复制”提示

#### Scenario: Clipboard API 不可用

- **WHEN** 浏览器不支持或拒绝标准 Clipboard API
- **THEN** 系统尝试兼容复制方式
- **AND** 若仍失败，显示明确的复制失败提示且不修改 Workspace
