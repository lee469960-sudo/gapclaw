# Task Plan: gap.version as pack + footer source

## Goal
Every package reads `deploy/gap.version` for image tags; in-app `/health` and sidebar/login footer display the same string.

## Next Step
Done.

## Current Phase
Phase 4

## Phases

### Phase 1: Requirements & Discovery
- [x] Footer is site-config text (Layout/Login), not `APP_VERSION`
- [x] `APP_VERSION` was hardcoded `1.9.0-clone`
- **Status:** complete

### Phase 2: Planning & Structure
- [x] File is source of truth for local and CI image tags
- [x] Resolve footer on read; version-only DB footer is replaced by current file
- **Status:** complete

### Phase 3: Implementation
- [x] `app/version.py` + health/auth/site-brand
- [x] Copy file into API image; pass `GAP_VERSION` build-arg
- [x] Release workflow + deploy.sh
- [x] Site.vue preview
- **Status:** complete

### Phase 4: Testing & Verification
- [x] `tests/test_app_version.py` — 6 passed
- [x] Live `/health` and `/site-brand.cgi` return `V0.0.3`
- **Status:** complete

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| DB footer was `V0.0.1`, would show `V0.0.1 · V0.0.3` | 1 | Treat version-only footer as stale and replace |
