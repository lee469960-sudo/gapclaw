# Findings

## Requirements
- Packaging (local + CI) reads `deploy/gap.version`
- UI footer matches that version
- Each package auto-updates the baked version (CI writes git tag into the file before docker build)

## Research Findings
- Sidebar `Layout.vue` `.version` binds `data.footer` from site config, not `APP_VERSION`
- `render.cgi` already returns `version` but the web never shows it
- API Docker context is repo root; web context is `apps/web` (footer comes from API at runtime)
- Production deploy currently requires lowercase `vMAJOR.MINOR.PATCH`; local tags use `V0.0.3`

## Technical Decisions
| Decision | Rationale |
|----------|-----------|
| `app/version.py` reads env `GAP_VERSION`, then `/app/deploy/gap.version`, then repo `deploy/gap.version` | Works in Docker and local uvicorn |
| `format_footer`: empty → version; custom appends ` · {version}` unless already present | Custom copyright stays; version always visible; DB stores raw footer |
| CI writes `${GITHUB_REF_NAME}` into `deploy/gap.version` then tags images from the file | Tag-triggered pack auto-updates version; everything still "reads the file" |
| deploy.sh accepts `v*` and `V*` | Local `V0.0.3` and git `v1.0.0` both valid |
