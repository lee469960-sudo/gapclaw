# Task Plan: code-agent-minimal-privilege-local-publish

## Goal

按 `openspec/changes/code-agent-minimal-privilege-local-publish/tasks.md` 实施最小权限、仓库自动发现的 local 发布流程。

## Source of Truth

OpenSpec `tasks.md` 是唯一正式任务来源。本文件只映射执行阶段，不重新定义需求。

## Current Phase

Phase 2: 最小权限依赖引导（in progress）

## Phases

1. 初始化与现状核对（任务映射：无）— complete
2. 移除标准发布档案复杂度（任务映射：1.1–1.2）— complete
3. 最小权限依赖引导（任务映射：2.1–2.2）— in progress
4. 仓库驱动的自动 local 发布（任务映射：3.1–3.2）— pending
5. 可观察性与回归（任务映射：4.1–4.2）— pending

## Verification Criteria

- 每项 OpenSpec 任务完成前均有对应代码和测试证据。
- Claude Code、Code Tool、发布入口始终以非 root 运行；root 仅可运行受限、无 shell 的安装 argv。
- 不保留平台档案、命令注册表、Manifest local 发布字段或专用发布 UI。
