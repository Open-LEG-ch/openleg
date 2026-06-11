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
- Put private operations and internal planning in a separate private repository.
