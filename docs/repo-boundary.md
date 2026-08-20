# Repository Boundary

This repository (`openleg`) is public and contains product runtime code.  
Keep private operational and internal planning content in a separate private repository.

## Main Branch Contract

- `main` is PR-only.
- No direct pushes to `main`.
- Required checks on PRs to `main`:
  - `ci/lint`
  - `ci/test`
  - `ci/security`

## Private Material

Do not commit internal planning, outreach execution, funding work, production
configuration, host inventory, incident notes, or secret-handling procedures.

## Rule of Thumb

- Put reusable product code, tests, and public docs in `openleg`.
- Put the dashboard and API runtime in `openleg`.
- Put the marketing website runtime, public directories, ranking and legal pages
  in `openleg-ops`; link to it through the `PUBLIC_SITE_URL` origin seam.
- Keep local handoff notes untracked.
- Put production status, incident logs, operations, and internal planning in `openleg-ops`.
