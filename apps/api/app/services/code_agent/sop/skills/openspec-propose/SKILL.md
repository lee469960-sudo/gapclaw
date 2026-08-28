---
name: openspec-propose
description: Create an OpenSpec change and all planning artifacts in one step.
compatibility: Requires openspec CLI.
---

Create a new OpenSpec change with proposal, specs, design, and tasks.

This CodeAgent run already authorized planning **and** later apply in the same run. After artifacts exist, do not wait for a human; the next SOP step is planning-with-files then apply.

Steps:

1. Derive a kebab-case change name from the objective.
2. `openspec new change "<name>"`
3. For each artifact, run `openspec instructions <id> --change "<name>" --json` and write the file.
4. `openspec status --change "<name>"` until proposal, specs, design, and tasks are done.
