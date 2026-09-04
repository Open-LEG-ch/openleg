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

## Community lifecycle

Issue #504 covered 70 mutants across `create_community_record`,
`mark_formation_started`, and `submit_community_to_dso`.

| Run | Total | Killed | Survived |
| --- | ---: | ---: | ---: |
| Baseline | 70 | 55 | 15 |
| After #504 | 70 | 70 | 0 |

Existing tests already pin creation defaults, legal and idempotent transition
predicates, status timestamps, and audit events. The added assertions pin the
success and failure diagnostics. Native verification killed all 15 targeted
survivors; none were classified as equivalent.

## Community read seams

Issue #505 covered the repository reads in `fetch_community_with_members`,
`fetch_user_communities`, and `fetch_nearby_consenting_neighbours`. Existing
tests pin join completeness, consent filters, distance boundaries, ordering,
and user parameters. The added failure-diagnostic assertions killed all 12
targeted survivors. The three-function slice moved from 26/38 to 38/38 killed;
none were classified as equivalent.
