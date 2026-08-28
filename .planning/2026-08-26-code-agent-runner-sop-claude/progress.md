# Progress Log

## Session: 2026-08-26

### Phase 1: OpenSpec artifacts

- **Status:** complete
- **Started:** 2026-08-26 23:29
- Actions taken:
  - Grilling 共识确认
  - `openspec new change code-agent-runner-sop-claude`
  - Wrote proposal, specs, design, tasks
- Files created/modified:
  - `openspec/changes/code-agent-runner-sop-claude/`

### Phase 2: Image + SOP assets

- **Status:** complete
- Actions taken:
  - Baked SOP skills + CLAUDE.md
  - Dockerfile pin OpenSpec 1.10.0 and COPY sop/
- Files created/modified:
  - `apps/api/app/services/code_agent/sop/`
  - `deploy/code-agent-runner.Dockerfile`

### Phase 3: Runtime

- **Status:** complete
- Actions taken:
  - RunnerSpec bridge for claude_code
  - Agent LLM exec env
  - Two-phase grill
  - SOP materialize
  - Archive after seal
- Files created/modified:
  - runner_protocol.py, runner.py, claude_code_runtime.py, control_plane.py, agent_chat.py, runtime.py

### Phase 4: Tests

- **Status:** complete
- Actions taken:
  - Added `tests/test_code_agent_coding_sop.py`
  - Updated runner/protocol/runtime_context tests
- Files created/modified:
  - tests listed above

### Phase 5: Image rebuild

- **Status:** complete
- Actions taken:
  - Official `deb.debian.org` apt failed (500/502). Rebuilt with Aliyun Debian mirrors.
  - Official npm hung; added portable `ARG NPM_REGISTRY` (default npmjs) and built with `registry.npmmirror.com`.
  - Image `code-agent-runner:1` RepoDigest `code-agent-runner@sha256:43d804671438d8d1d39edc78073763b440b0bd7f298e8a3dfc352d2d5bb21e5c` (later superseded by Phase 6 refresh digest).
  - Probe as `65534:65534` / read-only / network none: Claude Code `2.1.246`, OpenSpec `1.10.0`, SOP skills propose/apply/verify/archive + planning-with-files.
  - Kept old digest `aed8f761…` in `CODE_TRUSTED_IMAGE_DIGESTS` so published Manifests stay ready; added the new digest; set `CODE_CLAUDE_CODE_RUNTIME_ENABLED=true` in local `apps/api/.env` only.
  - Restarted API (health ok). Did **not** publish `dbt_test`.
- Files created/modified:
  - `deploy/code-agent-runner.Dockerfile` (`ARG NPM_REGISTRY`)
  - `apps/api/tests/test_code_agent_runner.py` (pin assertions allow `--registry`)
  - `apps/api/.env`, `deploy/.env` (digests; local flag)

## Test Results

| Test | Input | Expected | Actual | Status |
|------|-------|----------|--------|--------|
| SOP pytest suite | pytest -q runner_protocol, runner, claude_code_runtime, coding_sop, control_plane, control_plane_ui, runtime_context, policy_layers | pass | 233 passed | ✓ |
| Dockerfile pin test | test_runner_dockerfile_pins_claude_code_cli_version | pass | 1 passed | ✓ |
| Image probe | claude/openspec/sop as 65534 read-only | 2.1.246 / 1.10.0 / five skills | same | ✓ |

### Phase 6: Claude Code operational closure

- **Status:** complete
- Trigger:
  - User observed live CodeAgent runner still had no Claude Code and `/workspace` was not a Git repository.
- Fact check:
  - Live `code-agent-1fbadf24` uses retired digest `aed8f761…`.
  - `docker exec 1aa72e285303 git -C /workspace status` returned `fatal: not a git repository`.
  - Local pre-refresh image `code-agent-runner:1` had digest `43d804671438d8d1d39edc78073763b440b0bd7f298e8a3dfc352d2d5bb21e5c`.
- Artifact updates:
  - Added OpenSpec tasks 7.1–7.4 for Git workspace, commit prevention, old digest cleanup, and multi-arch guidance.
  - Updated design/specs to require real `/workspace/.git/`, retired digest cleanup, and production manifest-list digest.
- Current verification state:
  - 7.1 complete: `WorkspaceManager` now materializes a real `/workspace/.git/`, keeps `source.git` as a compatibility symlink, excludes `.git/**` from changed paths and sealed patch diff, and preserves `.git` during baseline reset.
  - 7.2 complete: runner helper and Code Tool shell reject direct `git commit`; SOP forbids commits; integrity guard fails if HEAD or `refs/heads/snapshot` moves.
  - 7.3 complete: local `apps/api/.env` and `deploy/.env` now trust only digest `5f2b4057…`; old `code-agent-1fbadf24` container using `aed8f761…` was deleted; host workspace `apps/api/data/code-agent/runs/1fbadf24/workspace` remains.
  - 7.4 complete: added `deploy/build-code-agent-runner.sh` for local and buildx multi-arch runner publication; `.env.example` and Manifest UI now instruct production to use registry `image@sha256:<manifest-list-digest>`.
- Image refresh:
  - Full Dockerfile rebuild was interrupted after repeated apt index stalls against `trixie/main arm64 Packages`.
  - Local refresh build used existing `code-agent-runner:1` as base and copied updated `runner_helper.py` / SOP plus `CODE_AGENT_GIT_DIR=/workspace/.git`.
  - New local `code-agent-runner:1` RepoDigest: `code-agent-runner@sha256:5f2b40575e43f22cb66cf724404854aefbda15f81915dc5f99824d219ff195e6`.
  - Probe passed: Claude Code `2.1.246`, OpenSpec `1.10.0`, `CODE_AGENT_GIT_DIR=/workspace/.git`, helper `DEFAULT_GIT_DIR=/workspace/.git`.
- Verification:
  - First 7.x test run: **1 failed, 157 passed** (`test_runner_multiarch_publish_script_documents_manifest_list_digest` assertion too narrow).
  - Focused rerun after test fix: **53 passed, 1 warning**.
  - Broader 7.x related suite before assertion fix: all functional tests passed; only the static script assertion failed.
  - Full 7.x broader suite after fixes: **334 passed, 184 warnings**.
  - `openspec validate code-agent-runner-sop-claude --strict` → valid.
  - `openspec instructions apply --change code-agent-runner-sop-claude --json` → **14/14 complete**, `state=all_done`.
  - `bash -n deploy/build-code-agent-runner.sh` → pass.
  - `apps/api` settings load `CODE_TRUSTED_IMAGE_DIGESTS=["code-agent-runner@sha256:5f2b40575e43f22cb66cf724404854aefbda15f81915dc5f99824d219ff195e6"]`.
  - `docker inspect code-agent-runner:1` → `code-agent-runner@sha256:5f2b40575e43f22cb66cf724404854aefbda15f81915dc5f99824d219ff195e6`.
  - `docker ps --filter name=code-agent-` → no running CodeAgent containers.
  - Old host workspace `apps/api/data/code-agent/runs/1fbadf24/workspace` still exists.

## Error Log

| Timestamp | Error | Attempt | Resolution |
|-----------|-------|---------|------------|
| 2026-08-26 | FakeRunner.exec rejected `environment=` | 1 | Optional kwarg on fakes |
| 2026-08-26 | CodeAgentRun NOT NULL manifest_id in env tests | 1 | Use SimpleNamespace |
| 2026-08-26 | Frozen model_config gained empty base_url | 1 | Only set base_url when non-empty |
| 2026-08-26 | deb.debian.org apt 500/502 | 1 | Aliyun DEBIAN_MIRROR |
| 2026-08-26 | npmjs hung on Claude Code | 1 | ARG NPM_REGISTRY + npmmirror |
| 2026-08-28 | `开始实现` failed with `model_unavailable` | 1 | Diagnosed Agent LLM binding: `dbt-test` uses LLM group `36bee1c3` with no direct key/model; added Phase 8 OpenSpec tasks |

### Phase 8: Claude Code model binding closure

- **Status:** complete
- Trigger:
  - User hit `Failure model_unavailable · Stage model · Claude Code 模型不可用`.
- Fact check:
  - `dbt-test` Agent (`8c862281`) is `profile=code`, project `ae834a47`.
  - Latest Manifest v6 is published with `coding_runtime=claude_code` and digest `5f2b4057…`.
  - `dbt-test.llm_id=36bee1c3` points to LLMResource `type=group`, with no direct API key/model.
- Actions taken:
  - Added OpenSpec tasks 8.1–8.3.
  - Runtime env resolution now returns specific reasons: `llm_not_configured`, `llm_not_found`, `llm_group_not_supported`, `llm_api_key_missing`, `llm_model_missing`.
  - Agent save now rejects Code Profile + `claude_code` project when bound LLM is group or lacks key/model.
  - Failure presentation maps `llm_group_not_supported` to stable `model_unavailable` while surfacing a specific actionable detail.
  - Current local DB probe: `claude_code_llm_binding_reason('36bee1c3')` → `llm_group_not_supported`.
- Verification:
  - `PYTHONPATH=apps/api pytest -q apps/api/tests/test_code_agent_coding_sop.py apps/api/tests/test_code_agent_control_plane_ui.py apps/api/tests/test_code_agent_results.py apps/api/tests/test_code_agent_failures.py apps/api/tests/test_code_agent_claude_code_runtime.py` → **133 passed, 99 warnings**.
  - `PYTHONPATH=apps/api pytest -q apps/api/tests/test_code_agent_control_plane.py apps/api/tests/test_code_agent_policy_layers.py apps/api/tests/test_code_agent_runtime_context.py apps/api/tests/test_code_agent_runner_protocol.py` → **127 passed, 35 warnings**.
  - `PYTHONPATH=apps/api pytest -q apps/api/tests/test_code_agent_*.py` → **653 passed, 4 skipped, 382 warnings**.
  - `openspec validate code-agent-runner-sop-claude --strict` → valid.
  - `openspec instructions apply --change code-agent-runner-sop-claude --json` → **17/17 complete**, `state=all_done`.

### Verify (2026-08-27)

- `openspec status` / `instructions apply`: schema `spec-driven`, 10/10 tasks complete.
- Re-ran SOP pytest gate → **233 passed**.
- No CRITICAL. Warnings: archive result not persisted to `runner_facts`; missing dedicated tests for no-archive-on-failure and legacy skip SOP; `bridge` code comments do not say “full egress”.

### Fix after verify CRITICAL (2026-08-27)

- Fixed archive evidence persistence:
  - `archive_openspec_after_seal(...)` result is now stored in `runner_facts.claude_code_openspec_archive`.
  - Stored value is recursively redacted before persistence.
  - Runtime commits the archive evidence after successful Sealer archive hook.
- Added/strengthened tests:
  - archive evidence persistence and redaction.
  - archive not called on Verifier failure.
  - archive not called on Sealer failure.
  - legacy runtime does not materialize coding SOP.
- Added runtime comment that Claude Code `bridge` means full egress, not a domain allowlist.
- Verification:
  - Targeted fix tests: **14 passed, 5 warnings**.
  - Full SOP pytest gate: **234 passed, 130 warnings**.
  - `openspec validate code-agent-runner-sop-claude --strict` → valid.
- Current assessment: CRITICAL fixed; ready for verify/archive decision.

## 5-Question Reboot Check

| Question | Answer |
|----------|--------|
| Where am I? | `code-agent-runner-sop-claude` complete: 17/17 tasks, CodeAgent suite 653 passed, OpenSpec strict valid |
| Where am I going? | Configure `dbt-test` with a single Cloud Claude-compatible LLMResource, restart API, then run `开始实现`; archive only if the user asks |
| What's the goal? | claude_code SOP runtime in trusted runner |
| What have I learned? | See findings.md |
| What have I done? | OpenSpec + code + tests + new local trusted digest `5f2b4057…`; production multi-arch push script ready but not pushed |
