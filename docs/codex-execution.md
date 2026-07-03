# Codex Execution Integration

How to run the `Execution` stage of the pipeline
(`Idea -> Research -> Prototype -> PRD -> Kanban -> Execution -> QA`)
with OpenAI Codex as the executor. The proven operating model is a
two-agent loop: an orchestrating agent (Claude) plans, writes the failing
tests, reviews, and verifies; Codex implements.

## Operating model (proven on issues #105 to #109)

Per slice, one issue, one branch, one PR:

1. The orchestrator picks the slice and writes the failing tests first
   (red), pinning the exact contract.
2. Codex implements until green:
   `codex exec --full-auto "<precise task: files, contract, DoD>"`.
   The task prompt names the failing test file, the mapping rules, and the
   constraints (no test edits, no built-asset edits, Schweizer Hochdeutsch
   copy rules).
3. The orchestrator reviews the diff hunk by hunk, fixes nits directly,
   drives the real app (local Postgres 16 + Redis) to verify behavior, and
   runs the full gate:

```bash
scripts/tdd_cycle.sh gate
```

4. The orchestrator opens the PR against `main` and addresses review
   feedback with the same red-test-first discipline.

Review Codex output critically: it is capable but will sometimes satisfy a
gate the cheap way (for example excluding a file from formatting instead of
formatting it). The orchestrator owns the gate.

## Environment requirements (CLI executor)

For a Claude Code cloud session driving Codex CLI:

- Install: `npm i -g @openai/codex`, then authenticate headlessly:
  `printenv OPENAI_API_KEY | codex login --with-api-key`.
- `OPENAI_API_KEY` must be set as an environment secret. Never commit it.
- The environment network policy must allow `api.openai.com`.
- GitHub write access (push, issues, PRs) for the session.
- Smoke test before first use: `codex exec --full-auto "list the files you
  can see, change nothing"` must exit cleanly with a clean `git status`.

## Conventions

- Branches: `codex/<slug>`. PR titles: `[codex]` prefix.
- `main` is PR-only; required checks are exactly `ci/lint`, `ci/test`,
  `ci/security`. QA stays human: a maintainer reviews every PR before merge.
- `AGENTS.md` at the repo root is the agent contract. It stays byte-identical
  to `CLAUDE.md`, enforced by `tests/test_docs_boundary_contract.py`. Change
  both together, never one alone.

## Alternative: Codex cloud tasks

For delegating whole tasks to Codex cloud instead of the in-session CLI:

1. Connect the GitHub org `open-leg-ch` in Codex settings and grant access
   to the `openleg` repository.
2. Create a Codex environment with setup script `scripts/codex_setup.sh`
   (Python 3.11 base). Tests are fully mocked and need no network, database,
   or secrets after setup.
3. Hand Codex an execution packet, not a vague goal: the PRD slice, the
   target branch, and the execution standard (TDD via `scripts/tdd_cycle.sh`,
   full gate before finishing).

## Boundary rules

Codex works only in this public repo. Deployment runbooks, host inventory,
secrets, and strategy material live in `openleg-ops` and are out of scope
for Codex tasks here.
