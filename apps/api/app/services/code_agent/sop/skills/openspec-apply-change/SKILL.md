---
name: openspec-apply-change
description: Implement tasks from an OpenSpec change.
compatibility: Requires openspec CLI.
---

Implement every pending `- [ ]` task in the change `tasks.md`.

1. `openspec instructions apply --change "<name>" --json`
2. Read context files listed there.
3. Implement each task, then mark it `- [x]`.
4. Keep going until all tasks are complete or blocked.
