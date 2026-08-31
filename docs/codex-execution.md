# External Executor Integration

How to run the `Execution` stage of the pipeline
(`Idea -> Research -> Prototype -> PRD -> Kanban -> Execution -> QA`)
with an external implementation executor. The operating model is a two-agent
loop: the orchestrator plans, writes the failing tests, reviews, and verifies;
Kimi Code implements almost all execution work.

## Operating model (proven on issues #105 to #109)

Per slice, one issue, one branch, one PR:

1. The orchestrator picks the slice and writes the failing tests first
   (red), pinning the exact contract.
2. Kimi Code implements until green:
   `kimi --print --no-thinking --prompt "<precise task: files, contract, DoD>"`.
   Unattended runs use a disposable isolated checkout without production
   credentials. They may change only the pull-request branch. Before any push,
   the orchestrator reviews the complete diff explicitly and runs the full
   validation gate.
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

Review executor output critically: it may sometimes satisfy a
gate the cheap way (for example excluding a file from formatting instead of
formatting it). The orchestrator owns the gate.

Run CodeRabbit on every committed slice before opening or updating its pull
request. Address every valid finding, add a regression test where appropriate,
and rerun until the review reports no findings. If the included quota is cooling
down, keep the branch local and resume the review when available.

If Kimi Code is unavailable, use Claude Code. If both are unavailable, use
ChatGPT 5.4. Record the fallback and reason in the execution handoff. The
orchestrator may apply tiny mechanical corrections directly after reviewing the
executor's diff.

## Environment requirements (CLI executor)

For a session driving Kimi Code CLI:

- Install and authenticate Kimi Code using its official CLI flow.
- Keep the Kimi API key in the environment or system credential store. Never
  commit it or paste it into logs.
- The environment network policy must allow the configured Kimi API endpoint.
- GitHub write access (push, issues, PRs) for the session.
- Smoke test before first use: `kimi --print --no-thinking --prompt "list the
  files you can see, change nothing"` must exit cleanly with a clean
  `git status`.

## Conventions

- Branches: `codex/<slug>`. PR titles: `[codex]` prefix.
- `main` is PR-only; required checks are exactly `ci/lint`, `ci/test`,
  `ci/security`. QA stays human: a maintainer reviews every PR before merge.
- Local agent contracts are private, untracked files. Never commit them to the
  public repository. Public engineering requirements belong in `CONTRIBUTING.md`
  or a focused document under `docs/`.

## Review Rubric

### Two axes: standards vs. spec

Every review finding gets tagged against exactly one of two independent
questions:

- **Standards** — does the diff violate repo engineering conventions (style,
  seam discipline via `database.get_connection`, security posture, test
  patterns)?
- **Spec** — does the diff actually implement the slice's contract, the one
  pinned by the failing test written before implementation?

Passing one does not imply passing the other: a diff can be clean,
idiomatic code that solves the wrong problem, or a correct behavior wrapped
in code that violates repo conventions. Conflating the two hides which
failure mode actually occurred, so review comments should say which axis a
finding belongs to.

### The deletion test

Before a new helper, module, or abstraction survives review, ask: if this
were deleted, would anything break, or would nothing notice? A wrapper with
exactly one caller that nothing else depends on should usually be inlined
rather than kept. Complexity that vanishes when a module is deleted was
pass-through cruft; complexity that reappears elsewhere was load-bearing and
earns its keep.

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
