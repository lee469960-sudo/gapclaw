Follow this SOP for this CodeAgent run. The user already completed grill outside the sandbox. The run objective is authorization to finish the chain below without waiting for another human.

1. `/opsx:propose` (openspec-propose skill) — create the OpenSpec change and planning artifacts.
2. planning-with-files — keep `.planning/<date>-<change>/task_plan.md`, `findings.md`, `progress.md` and `.planning/.active_plan` updated.
3. `/opsx:apply` (openspec-apply-change) — implement the tasks.
4. Implement until the code matches the change.
5. `/opsx:verify` (openspec-verify-change) — then STOP.

Do not run `/opsx:archive` or `openspec archive`. The platform Sealer archives only after a successful seal.
Do not grill the user; there is no operator in this sandbox.
Do not treat your own "done" text as delivery. Platform Verifier and Sealer decide success.
Do not run `git commit`, move HEAD, or rewrite Git refs. Use Git only for status, diff, and log context; the platform delivers a verified patch artifact.
