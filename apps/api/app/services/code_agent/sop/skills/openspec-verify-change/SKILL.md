---
name: openspec-verify-change
description: Verify implementation matches the OpenSpec change artifacts.
compatibility: Requires openspec CLI.
---

Check that implementation matches proposal, specs, design, and tasks.

1. Read `openspec/changes/<name>/` artifacts.
2. Confirm each tasks.md checkbox that is `[x]` is actually implemented.
3. Run the tests those tasks name.
4. Report gaps. Do not archive.
