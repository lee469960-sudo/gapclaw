## Context

See `proposal.md` for motivation and the delta specs for behavior. The CodeAgent runtime already persists `CodeProject`, `CodeProjectManifest`, `CodeAgentRun` and sealed artifact records, and enforces project ACLs at run admission. Those records currently have no dedicated management route or UI. The existing Agent API accepts `profile` and `code_project_id`, but `Agents.vue` neither renders nor submits them.

The design must expose control-plane configuration without duplicating or weakening the existing admission, Workspace, runner, Verifier or artifact-review gates. Existing Standard Agents remain independent of this surface.

## Goals / Non-Goals

**Goals:**

- Provide an RBAC-protected Code Project management surface with project ACLs and availability state.
- Make Manifest drafts editable, publish only immutable validated versions, and preserve the current published version on failure.
- Let authorized users convert an existing Agent to Code Profile through the normal Agent editor.
- Give operators enough visible status to correct configuration failures before submitting a Code run.

**Non-Goals:**

- Changing Code run policy merging, runner isolation, verifier behavior, artifact acceptance or Git-write restrictions.
- Adding arbitrary repository discovery, auto-generating Manifest values, or allowing a user to bypass policy through UI input.
- Turning Standard Agent tools, Sandbox Workspace content or direct Shell permission into CodeAgent capabilities.
- Editing a published Manifest in place or mutating an already frozen run contract.

## Decisions

### 1. A dedicated Code Project page owns project and Manifest lifecycle

Add one page-level Code Project control-plane route, menu entry and matching frontend view. Its API exposes list, detail, create, update, enable/disable, Manifest draft save, publish and historical-version read actions. The existing Agent route remains responsible only for Agent configuration and consumes a compact project reference/status response.

This centralizes policy-bearing fields, prevents the Agent editor from becoming a Manifest editor, and matches the platform's existing page-router/RBAC structure.

**Alternatives considered:**

- Put all Manifest fields in the Agent dialog: rejected because one project can be used by several Agents and a per-Agent policy editor would obscure ownership and risk divergent configuration.
- Use direct database setup or an undocumented API: rejected because users cannot safely discover validation, ACL or publishing state.

### 2. Page RBAC and project ACL are separate gates

The new page is governed by its own page permission. Project-level read/edit actions additionally require the existing project ACL: privileged project managers can manage projects, while project creators and explicitly authorized users can see only permitted project data. The server is authoritative for every action; UI filtering is only a convenience.

This preserves the existing ACL model while avoiding disclosure of repository or Manifest data to users who merely know an identifier.

**Alternatives considered:**

- Rely solely on the menu permission: rejected because a user with the menu could enumerate projects they do not own.
- Rely solely on project ACL: rejected because project management needs an explicit administrative capability.

### 3. Manifest editing uses draft-to-publish versioning

An editable draft represents one candidate next Manifest version. Publishing revalidates the complete draft using the same control-plane validation required for run admission, then creates an immutable published version atomically. The previously published version remains active if draft validation or persistence fails. Historical published versions are read-only; creating a revision starts a new draft.

This produces reviewable history and ensures the UI cannot create a transient “published but invalid” state.

**Alternatives considered:**

- In-place editing of the active Manifest: rejected because it would undermine reproducibility and change the meaning of configuration while users review it.
- Treat every save as published: rejected because incomplete configuration must be editable without gaining execution authority.

### 4. Project availability is a server-computed read model

The control-plane API computes a normalized availability object for each visible project: `ready` or an explicit reason such as disabled project, unsupported environment tier, absent published Manifest or invalid published Manifest. The Agent editor and project page display this object; Code run admission continues to independently perform its existing authoritative checks.

One server-computed state prevents UI/API drift while retaining defense in depth at run creation.

**Alternatives considered:**

- Reimplement readiness checks in the browser: rejected because policy rules and validation can change and client results are not authoritative.
- Hide unavailable projects without explanation: rejected because it leaves operators unable to correct their configuration.

### 5. Profile-specific Agent UI is conditional and preserves Standard form semantics

The Agent form adds a Profile selector with `standard` as its default. Selecting `code` reveals the project selector and an explanatory summary of the managed CodeAgent boundary; saving is disabled until a ready, authorized project is selected. Selecting `standard` hides and does not require a project. The server receives `profile` and `code_project_id` only as normal Agent fields and retains current validation as the final gate.

The Agent card displays Code Profile and project status so operators can distinguish it from a Standard Agent without inspecting the dialog. Code-run result UI stays on the existing Agent Chat surface and presents only sealed/verified artifacts as adoptable.

**Alternatives considered:**

- Separate CodeAgent type and route: rejected because it would fork the unified Agent model and make converting an existing Agent unnecessarily disruptive.
- Auto-promote an Agent based on attached Shell/File permissions: rejected because these are unrelated Standard capabilities and would create an implicit privilege escalation path.

## Risks / Trade-offs

- [Manifest form is complex] → Group fields by repository, policy, validation and budgets; preserve drafts and return field-level server validation errors.
- [ACL changes can leave a previously configured Agent unusable] → Show the availability reason at edit and submit time; keep run admission authoritative and fail closed.
- [Published-version history grows] → Retain immutable metadata and expose concise history; this change does not add destructive history deletion.
- [UI status becomes stale] → Refresh availability on dialog open, project save/publish and task submission; admission still verifies fresh state.
- [New page permission may be missing on existing roles] → Startup role synchronization grants the new page to built-in administrative roles; other roles require deliberate assignment.

## Migration Plan

1. Add the page permission, backend control-plane route and read model without changing existing Agent behavior.
2. Add the project/Manifest UI and verify draft, publish, ACL and availability cases.
3. Extend Agent form/list and Chat presentation; deploy with Profile defaulting to `standard`.
4. On rollback, remove the menu/UI exposure and reject new control-plane writes while retaining existing project, Manifest and sealed-run records; Standard Agent availability is unaffected.
