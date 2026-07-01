# PRD — Matt Pocock-style Quality Slices

- Repo: `openleg`
- Branch: `codex/matt-pocock-quality-slices`
- Status: ready for execution
- Source: type/contract discipline push without overbuilding

## Goal

Improve type and contract clarity at small, high-leverage boundaries and remove the Redis 8 deprecation warning caused by `setex`. No user-facing behaviour change.

## Non-goals

- No TypeScript migration or build pipeline.
- No new runtime dependencies or dev dependencies.
- No framework changes, no abstraction layers for their own sake.
- No production secrets or host-specific config.

## Constraints

- `main` is PR-only; required checks: `ci/lint`, `ci/test`, `ci/security`.
- Thin TDD per slice: red -> green -> refactor.
- Schweizer Hochdeutsch for any user-facing text (none expected here).
- Each slice ships as its own commit/PR where possible.

## Quality gates

```bash
python3 -m ruff format --check .
python3 -m ruff check .
python3 -m pytest tests/ -q
```

## Slices

### US-001 — Replace deprecated Redis `setex` call

- File: `cache.py`
- Test: `tests/test_cache.py`, `tests/test_tenant_cache.py`
- Change: `redis.setex(key, ttl, value)` -> `redis.set(key, value, ex=ttl)`.
- Contract test must assert the exact call signature and prove `setex` is not used.
- Labels: `discipline`, `dependencies`, `python`.

### US-002 — Add JSDoc / `@ts-check` contract checks to `leg_kalkulator` inline JS

- File: `templates/leg_kalkulator.html` (or the inline script serving the calculator)
- No build step; add `// @ts-check` and minimal JSDoc so `tsc --noEmit --allowJs --checkJs` (run via local TypeScript if already installed) surfaces contract mismatches.
- Keep behaviour identical; types are documentation + guardrails.
- Labels: `discipline`.

### US-003 — Add public JSON endpoint contract tests

- Files: `api_public.py`, `tests/test_api_public.py`
- Contract tests for status endpoint(s), expected keys, and no traceback leakage in error responses.
- Labels: `discipline`, `python`.

### US-004 — Add one TypedDict/dataclass view-model contract for a Python domain boundary

- Candidate boundary: `municipality_profile.public_profile(bfs)` return shape or `registration` response payload.
- Add one `typing.TypedDict` or `@dataclass` and a test that asserts the produced dict/object matches the declared fields.
- Labels: `discipline`, `python`.
