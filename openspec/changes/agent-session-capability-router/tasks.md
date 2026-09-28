## 1. Existing route audit and regression baselines

- [ ] 1.1 Audit current `execution_policy`, `_bound_mcp_capability_hints()`, `mcp_routing.py`, Skill loading, and related tests to document where bound MCP / Skill matching fails today.
- [ ] 1.2 Add regression tests for known failure cases: bound MCP keyword/alias mismatch, Chinese phrase mismatch, Skill-bound request falling into chat fast path, and explicit capability mention not becoming an advisory candidate.
- [ ] 1.3 Verify existing MCP guarantees remain covered before changes: all bound callable MCPs are candidates, missing metadata does not exclude MCPs, empty route falls back to all eligible candidates, and unbound resources stop explainably.

## 2. Routing hints model and configuration

- [ ] 2.1 Locate existing MCP, Skill, and Agent binding configuration models/APIs and add optional persisted routing hints only where needed; verify existing Agents without hints still load, save, and run unchanged.
- [ ] 2.2 Implement effective routing hint merge semantics for global capability defaults plus Agent binding overrides; verify enabled/description override and keywords/aliases/tags union behavior with unit tests.
- [ ] 2.3 Add validation/sanitization for routing keywords, aliases, tags, description, and enabled state; verify malformed hints are ignored or normalized without changing execution permissions.
- [ ] 2.4 Seed hand-authored routing hints for selected built-in/test MCP and Skill capabilities only; verify user-created capabilities are not automatically assigned generated keywords.

## 3. Existing route matching optimization

- [ ] 3.1 Extend the existing pre-ReAct route path to classify `normal_chat`, `capability_candidate`, `explicit_capability_request`, `missing_capability`, and `blocked_capability`; verify representative messages produce the expected mode without adding an LLM classifier.
- [ ] 3.2 Extend deterministic matching/scoring using existing name/tags/description plus optional aliases/keywords/tags, action-intent bonus, Chinese substring matching, and short-English token-boundary matching; verify scoring and ordering with unit tests.
- [ ] 3.3 Extend Skill route hints so bound Skills can become advisory candidates without reading Skill.md; verify later Skill loading still uses existing runtime mechanisms.
- [ ] 3.4 Preserve existing MCP route authority: `build_mcp_route_candidates()` still includes all bound callable MCPs, optional advisory TopK does not shrink the semantic-routing candidate pool, and empty-route fallback behavior remains unchanged.
- [ ] 3.5 Implement safe degradation for malformed hints or matching exceptions; verify the runtime falls back to the existing safe route outcome, emits no unsafe candidates, and records sanitized error trace.

## 4. ReAct context integration and trace

- [ ] 4.1 Integrate advisory Candidate Capabilities before ReAct context assembly; verify `normal_chat` injects no Candidate Capabilities block and does not connect MCPs, list tools, or load Skills because of the router.
- [ ] 4.2 Inject structured Candidate Capabilities context and a short advisory instruction for `capability_candidate` and `explicit_capability_request`; verify ReAct receives candidate type, name/id, score, hit terms, reason, and description without full tool directories or secrets.
- [ ] 4.3 Persist sanitized route trace in existing progress/event and message or run metadata surfaces; verify trace includes route mode, matched terms, visible candidate ids, blocked/missing reasons, injected candidate count, MCP route decision summary, and excludes prompts, credentials, tool directories, and Chain of Thought.
- [ ] 4.4 Add or update debug/read-only UI/API surfaces for route trace inspection; verify operators can diagnose why a capability was or was not considered without exposing unauthorized global capability names to ordinary user context.

## 5. Compatibility and regression verification

- [ ] 5.1 Add runtime tests proving ordinary chat remains unpolluted by candidate context, Skill loading, MCP connection, and tool-list expansion.
- [ ] 5.2 Add tests for missing-capability and blocked-capability behavior, including non-leakage of unauthorized global capability names in ordinary ReAct context.
- [ ] 5.3 Add tests proving Candidate Capability injection remains advisory and ReAct/preflight can still choose not to call MCP or Skill.
- [ ] 5.4 Run affected backend suites for Agent runtime, execution policy, MCP routing/preflight, Skill loading, scheduled-task runtime context, and conversation fast path; record compatibility, idempotency, and rollback findings.
- [ ] 5.5 Run OpenSpec validation with `openspec validate agent-session-capability-router --strict` and repository diff checks; verify all change artifacts are complete before apply is considered done.
