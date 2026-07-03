# Codex Execution Integration

How to run the `Execution` stage of the pipeline
(`Idea -> Research -> Prototype -> PRD -> Kanban -> Execution -> QA`)
with OpenAI Codex against this repo.

## One-time setup (Codex side)

1. Connect the GitHub org `open-leg-ch` in Codex settings and grant access to
   the `openleg` repository.
2. Create a Codex environment for `open-leg-ch/openleg`:
   - Base image: universal (Python 3.11 available)
   - Setup script: `scripts/codex_setup.sh`
   - Agent internet access: off is fine after setup; tests are fully mocked
     and need no network, database, or Redis.
3. No secrets are required. The test suite mocks the database
   (`tests/conftest.py` sets `DATABASE_URL=""` plus test tokens). Never add
   production credentials to a Codex environment for this repo.

## What Codex reads

- `AGENTS.md` at the repo root is the agent contract. It is byte-identical to
  `CLAUDE.md` and enforced by `tests/test_docs_boundary_contract.py`. Change
  both files together, never one alone.

## Handing off a task

Give Codex an execution packet, not a vague goal:

- Link or paste the PRD slice (see `docs/matt-pocock-quality-slices.md` for the
  format: repo, branch, status `ready for execution`, small slices).
- State the target branch. Codex branches use the `codex/<slug>` prefix and PR
  titles use the `[codex]` prefix (see PR #93 for a merged example).
- Require the execution standard: TDD first (red -> green -> refactor) via
  `scripts/tdd_cycle.sh`, then the full gate before finishing:

```bash
scripts/tdd_cycle.sh gate
```

## Merge path

- `main` is PR-only; Codex must open a PR, never push to `main`.
- Required checks are exactly `ci/lint`, `ci/test`, `ci/security`.
- QA stays human: review the Codex PR before merge.

## Boundary rules

Codex works only in this public repo. Deployment runbooks, host inventory,
secrets, and strategy material live in `openleg-ops` and are out of scope for
Codex tasks here.
