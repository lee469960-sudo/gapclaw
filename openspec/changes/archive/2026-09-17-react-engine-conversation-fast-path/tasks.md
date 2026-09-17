## 1. Execution policy and configuration

- [x] 1.1 Add centralized per-mode execution policy with `chat` one-call plus one-repair defaults, task/tool budgets, no-progress limit, and `human_wait` suspension; verify configuration unit tests cover defaults and overrides
- [x] 1.2 Add lightweight request-mode routing with explicit tool/file/code/deploy intent precedence and chat fallback; verify routing tests classify ordinary prompts and explicit operations correctly

## 2. Runtime loop behavior

- [x] 2.1 Implement the chat fast path that accepts valid text without FINAL/PLAN or completion review; verify ordinary conversation integration tests perform one LLM call
- [x] 2.2 Add canonical output/state fingerprints and two-round no-progress/repetition stop handling; verify identical and semantically similar repeated outputs stop with an observable reason
- [x] 2.3 Apply independent retry and turn budgets to chat/task/tool/human_wait while preserving existing MCP/tool protocols; verify task/tool regression tests retain current behavior and human_wait makes no further LLM call
- [x] 2.4 Route genuine uncertainty, ambiguity, permission, and high-risk operations to human_wait without escalating repetition-only chat stops; verify human-loop scenarios and safe-stop scenarios

## 3. Observability and UI events

- [x] 3.1 Emit redacted route mode, turn count, retry count, stop reason, and human-loop reason in the terminal audit event; verify serialization and sensitive-field tests
- [x] 3.2 Expose compact progress/status events while retaining expandable detailed traces; verify event ordering and that repeated model text is not duplicated in the default summary

## 4. Verification and rollout

- [x] 4.1 Run focused runtime tests for routing, budgets, duplicate detection, and human loop; record results in progress.md
- [x] 4.2 Run the full React/Agent regression suite and static validation; confirm existing MCP lazy-loading and tool-task tests pass
- [x] 4.3 Exercise representative ordinary chat, explicit tool request, repeated output, ambiguous request, and high-risk request flows; document observed metrics and rollback switch
