# Formation mutation survivor classification

This record covers native `mutmut 3.7.0` runs for issue #500. The configured,
unfiltered run was executed in a clean copy with the repository virtual
environment.

## Result

| Slice | Total | Killed | Survived |
| --- | ---: | ---: | ---: |
| Baseline | 47 | 40 | 7 |
| After #500 | 47 | 46 | 1 |

The slice contains `start_formation` and `get_formable_clusters`. Tests now pin
the error and warning diagnostics on refused entry paths, killing all six
`start_formation` survivors. One behavioral equivalent remains:

| Mutant | Mutation | Why behavior is unchanged |
| --- | --- | --- |
| `formation_wizard.x_get_formable_clusters__mutmut_25` | Calculate readiness with `len(nearby) + 2` instead of `len(nearby) + 1`. | The enclosing guard admits only `len(nearby) >= min_size - 1`; both expressions are therefore `True` for every reachable result. |

Behavior tests pin unreadable and insufficient member counts, a successful
transition, a refused already-started or invalid transition, and the cluster
eligibility boundary. Killing the remaining mutant would require evaluating
code excluded by the enclosing guard.

## Community status

Issue #501 started with four survivors among 74 `get_community_status` mutants:
`#71`, `#72`, `#73`, and `#74`. They altered the exception diagnostic. The status tests
now pin that diagnostic alongside the not-found result, member classification,
readiness boundaries, and next steps for every formation state. Native
`mutmut 3.7.0` verification killed all four, taking the slice from 70/74 to
74/74 killed.

## Municipality business case

Issue #502 started with seven survivors among 101
`calculate_municipality_business_case` mutants: `#14`, `#16`, `#19`, `#75`,
`#83`, `#97`, and `#99`. All seven are behavioral equivalents:

- `#14`, `#16`, and `#19` change the fallback for `annual_savings_chf`, but
  `calculate_savings_estimate` always returns that key.
- `#75` and `#83` round monetary values to three places instead of two. The
  source value is already rounded to two places, and multiplying it by the
  integer household count cannot add a fractional decimal place.
- `#97` and `#99` change the fallback for `assumptions`, but
  `calculate_savings_estimate` always returns that key.

The focused tests pin household and aggregate savings, yearly projections,
rounding, CO2 totals, assumptions, defaults, and a zero-community plan. The
native slice remains 94/101 killed because no public input can reach the three
changed fallbacks or distinguish the two rounding expressions.
