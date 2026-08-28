## Context

See proposal.md for motivation. Existing `claude_code` adapter already runs `claude -p` inside the per-run runner, materializes DB-bound skills into `.claude/skills/`, and hands off to Verifier/Sealer. Gaps: `RunnerSpec` hard-requires `network_mode=none`; exec env has no LLM credentials; `model_ref` is the platform LLM id; `submit_chat` always `create_code_run`; live image digest predates the Claude Code Dockerfile; OpenSpec/planning skills are not in the image.

`code-agent-coding-runtime` lives only in an unarchived sibling change, not in main specs. This change adds `code-agent-coding-sop` instead of duplicating that capability.

## Goals / Non-Goals

**Goals:**

- Bake OpenSpec CLI `@fission-ai/openspec@1.10.0` and SOP skill files into the trusted runner image; keep Claude Code pin `2.1.246`.
- Allow `bridge` only when frozen runtime is `claude_code`; keep legacy on `none`.
- Inject Agent LLMResource into Claude Code exec env; use `model` as `--model` and `base_url` as `ANTHROPIC_BASE_URL`.
- Gate `claude_code` chat behind grill + exact `开始实现`.
- After successful seal, `docker exec` `openspec archive` for the change Claude created.

**Non-Goals:**

- Egress proxy / domain allowlist.
- Separate Claude credential store.
- UI confirm button.
- Publishing `dbt_test` or flipping platform `coding_runtime` default.
- Changing default `code_claude_code_runtime_enabled` to true in code (local `.env` only).

## Decisions

### 1. Honest `bridge`, not fake allowlist

Docker cannot domain-filter. `claude_code` uses `network_mode=bridge`. Specs and comments MUST say this is full egress.

Alternatives considered: host proxy (deferred), keep `none` (makes Q1 impossible).

### 2. SOP files live in the API package and are COPY'd into the image

API materializes into the host-visible workspace before/at start. Source: `apps/api/app/services/code_agent/sop/`. Dockerfile copies the same tree to `/opt/code-agent/sop`. planning-with-files SKILL is a sandbox-safe copy (no `$HOME` hooks); planning instructions also sit in `CLAUDE.md`.

Alternatives considered: bind `~/.claude/plugins` (rejected); only image path (API in a different container could not read it).

### 3. Two-phase chat without a Code run

If published Manifest + feature flag would yield `claude_code` and the message is not exactly `开始实现`, `submit_chat` enqueues `run_agent` with `code_run_id=None` and a grill-phase flag so `_run_agent_impl` does not require a run and does not start Docker. Tools stay the Agent's conversational set (no `code_*`). System prompt includes grilling SOP and tells the model to end with the exact confirmation instruction.

On `开始实现`, `create_code_run` uses prior session user text (excluding the confirmation line) plus assistant grill turns as `objective`.

If flag is off or Manifest is legacy, keep today's immediate `create_code_run`.

### 4. Credentials only in `exec` environment

Extend `CodeContainerRunner.exec` with an optional env map used only for Claude Code invocation. Do not pass secrets into `_build_claude_code_command`. Redact env keys from any logged command.

### 5. Archive after seal via runner exec

After `CodeArtifactSealer.seal()` returns sealed success, if runtime is `claude_code`, exec `openspec list` / `openspec archive` in `/workspace`. Failure to archive MUST NOT unseal or downgrade `patch_ready`; record a non-secret fact. Claude's `CLAUDE.md` forbids in-session archive.

### 6. Claude Code sees a normal Git workspace

Claude Code runs in `/workspace`, so `/workspace` MUST be a normal Git worktree with a real `.git/` directory. The sanitized metadata from the sealed snapshot is copied into `/workspace/.git`; `run_root/source.git` is kept only as a compatibility pointer for existing platform verifier/sealer/helper paths. `.git` is run-local sanitized metadata, contains no remotes, hooks, alternates, or credentials, and is ignored by workspace content snapshots and sealed patch diffs.

Claude Code may use Git for context (`status`, `diff`, `log`) but MUST NOT create commits. The platform blocks `git commit` through approved Code Tool shell policy and Verifier detects any HEAD movement before seal.

### 7. Trusted runner digest migration

The active trusted runner image for `claude_code` MUST be the new image that contains Claude Code, OpenSpec CLI, and SOP assets. Old runner digests that predate Claude Code are removed from local trusted configuration and old `code-agent-*` containers using the retired digest are deleted. Published Manifests are not mutated in place; a historical Manifest that references a retired digest becomes visibly non-runnable until republished.

Production multi-architecture publication MUST use a registry `image@sha256:<manifest-list-digest>` reference. Bare `sha256:<digest>` remains acceptable in tests/local fixtures, but production operator docs and UI guidance MUST prefer full image references.

### 8. Claude Code model binding is fail-closed and single-LLM only

`claude_code` uses the Agent-bound LLM only to supply Claude Code `--model`, `ANTHROPIC_API_KEY`, and optional `ANTHROPIC_BASE_URL`. The runtime MUST NOT resolve LLM groups or choose fallback members implicitly. A Code Profile Agent attached to a project whose latest published Manifest uses `coding_runtime=claude_code` MUST bind a single LLMResource with a decryptable API key and non-empty model.

Until there is an explicit Anthropic-compatible gateway contract, provider/base_url are not used as a hard compatibility gate. This keeps MVP validation schema-free while still preventing the observed failure class: an Agent bound to an LLM group with no direct key/model.

`model_unavailable` remains the stable failure type. The reason MUST be specific enough for UI/actionable diagnosis, including `llm_group_not_supported`, `llm_api_key_missing`, and `llm_model_missing`.

## Risks / Trade-offs

- [Full egress] → Document; do not claim allowlist; leftover flag still defaults false.
- [MiniMax or non-Anthropic Agent LLM] → Claude CLI may still fail against that `base_url`; fail closed as `model_unavailable` / `coding_failed`.
- [LLM group bound to a Code Agent] → Reject before save/run for `claude_code`; runtime still fails closed with a specific reason if older records bypass validation.
- [Grill confirmation spoof] → Exact string match only; other messages continue grill.
- [openspec archive after container freeze] → Unfreeze or exec while container still exists, before cleanup; if already frozen, thaw for archive exec then cleanup as today.
- [Writable `.git`] → Required for Claude Code usability. It is a per-run sanitized copy; Verifier blocks HEAD/ref drift and sealed artifacts ignore metadata.

## Migration Plan

1. Land code + tests.
2. Rebuild `code-agent-runner:1`; for production build and push a multi-arch image, then set `CODE_TRUSTED_IMAGE_DIGESTS` to the registry manifest-list `image@sha256` digest.
3. Set `CODE_CLAUDE_CODE_RUNTIME_ENABLED=true` in local API env.
4. Remove retired pre-Claude-Code digests from local trusted config and delete old `code-agent-*` containers using those digests.
5. Operator republishes any project that should use `claude_code`; historical published Manifests are not mutated.
6. Rollback: flag off and/or Manifest `legacy`; re-add the old digest only if a legacy Manifest must be temporarily run.

## Open Questions

None. Grilled consensus is the source of product decisions.
