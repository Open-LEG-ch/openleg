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
