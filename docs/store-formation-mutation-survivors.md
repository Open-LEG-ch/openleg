# Store formation mutation results

This record covers native `mutmut 3.7.0` runs for the formation repository. The
configured, unfiltered run was executed in a clean copy with the repository
virtual environment.

## Invitation flow

Issue #503 covered 67 mutants across `insert_invited_member`,
`confirm_invited_member`, and `count_confirmed_members`.

| Run | Total | Killed | Survived |
| --- | ---: | ---: | ---: |
| Baseline | 67 | 51 | 16 |
| After #503 | 67 | 67 | 0 |

The behavior tests pin duplicate detection, lookup and insert parameters,
confirmation from the invited state, confirmed-member counting, audit events,
and success, warning, and failure diagnostics. Native verification killed all
16 targeted survivors; none were classified as equivalent.
