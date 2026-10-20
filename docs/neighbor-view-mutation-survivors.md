# Neighbor view mutation survivor classification

These are historical results from the earlier #478 working branch. Mutant IDs and counts need a fresh unfiltered run at the current source revision. These classifications do not change native statuses or the automated gate.

Record covers native `mutmut 3.7.0` runs for `neighbor_view.py` (issue #498,
parent #478), run from a clean clone with the repository venv:

```bash
python -m mutmut run "neighbor_view.*"
python -m mutmut results
```

## Result

| Run | Total | Killed | Survived |
| --- | ---: | ---: | ---: |
| Baseline `jitter_coordinates` slice | 59 | 48 | 11 |
| After #498 | 59 | 54 | 5 |

The 11 baseline survivors were `jitter_coordinates` mutants #5, #6, #8, #9,
and #10, #15, #23, #38, #49, #50, #51.

## Codec alias

| Mutant | Mutation | Why behavior is unchanged |
| --- | --- | --- |
| `neighbor_view.x_jitter_coordinates__mutmut_15` | Encode the seed with `"UTF-8"` instead of `"utf-8"`. | Python codec names are case-insensitive aliases of one registry entry; `str.encode("UTF-8")` is byte-identical to `str.encode("utf-8")` for every input, so the derived seed value and every jittered point are unchanged. |

## Historical numerical proposals (unverified)

The rows below are historical unverified proposals carried over from the
earlier classification. They establish no equivalence proof, grant no
exemptions, and do not change the native survivor statuses recorded above.

| Mutant | Mutation | Review status |
| --- | --- | --- |
| `neighbor_view.x_jitter_coordinates__mutmut_5` | Treat a zero radius as a zero-distance jitter instead of returning early. | Earlier bit-identical claim withdrawn; clamping/wrapping can round the longitude even at zero distance (e.g. 8.55). Equivalence to the public anonymity contract is not established. |
| `neighbor_view.x_jitter_coordinates__mutmut_38` | Use earth radius `6378138.0` instead of `6378137.0`. | Changing the earth-radius constant changes numerical results; public-contract significance is not verified. |
| `neighbor_view.x_jitter_coordinates__mutmut_49` | Guard `abs(denom) <= 1e-9` instead of `< 1e-9`. | Earlier pole/impossibility argument withdrawn; the actual pole product is `3.905482530786651e-10` and the float `1e-9` is an approximation. Reachability of the exact guard boundary is not established. |
| `neighbor_view.x_jitter_coordinates__mutmut_50` | Use the pole fallback whenever `abs(denom) < 1.000000001` instead of `< 1e-9`. | The changed pole-fallback domain may change numerical results; public-contract significance is not verified. |

Malformed seeds, removed seed normalization, a positive radius that did not
jitter, a broken unseeded path, a stalled polar longitude, and a crashing pole
guard are not classified as equivalent. The behavior tests in
`tests/test_neighbor_view_privacy.py` kill those mutants.

## Unresolved longitude wrap

Mutant `neighbor_view.x_jitter_coordinates__mutmut_73` wraps longitude with
`(lon + delta - 180.0) % 360.0 - 180.0` instead of
`(lon + delta + 180.0) % 360.0 - 180.0`. The earlier classification claimed
the two expressions are bit-identical; that claim is withdrawn. A verified
counterexample: latitude `0.0`, longitude `-179.99999999999997`, and a
zero-distance RNG sample return that longitude in the original but `-180.0`
in the mutant, a difference of `2.842170943040401e-14` degrees. Whether this
difference matters for the public anonymity contract is unresolved; no
privacy defect has been demonstrated. The mutant keeps its raw native
survivor status and no exemption is claimed for it.

## Provisional matching

Issue #499 started with seven survivors in `find_provisional_matches`: #24, #25,
and #30, #37, #38, #43, and #44. Behavior tests pin inclusion at 150 metres,
exclusion beyond 150 metres, the profiles sent to the autarky calculation, and
the complete public summary. A historical report from that effort says a
native mutmut 3.7.0 run killed all seven; the source revision and run
metadata for that verification are not recorded here.

## Recheck after the BFS rewrite (#591) and #671 (historical, provisional)

The #591 rewrite replaced distance matching with municipality matching, which
invalidated the old classifications and left eleven `find_provisional_matches`
survivors, three new `jitter_coordinates` survivors, and one
`collect_building_locations` survivor. #671 removed the orphaned `_coordinates`
helper whose six mutants had become the module's uncovered block.

The recheck account is historical and provisional: its source revision and
native output are not recorded here. It classified `#73` and four older
jitter mutants (`#15`, `#38`, `#49`, `#50`) as equivalents; the `#73`
classification is withdrawn above. It reported fourteen other survivors
killed by behavior tests in `tests/test_neighbor_view_privacy.py`; fresh
native verification of those historical results is not claimed here:

- `find_provisional_matches` #11-#14 and #21-#26: the self-exclusion tests
  (`test_provisional_match_excludes_only_the_callers_own_building`) exercise a
  caller with a truthy `building_id` whose own stored row precedes a real
  neighbour's row. Every mutant either stopped scanning at the own row,
  skipped every row, or re-appended the caller's row; the pinned autarky
  input `[caller, other]` distinguishes all of them.
- `find_provisional_matches` #20:
  `test_provisional_match_scans_past_a_foreign_municipality_row` places a
  foreign-municipality row before the matching row; a `break` there would
  hide the real neighbour.
- `jitter_coordinates` #62 and #67: the latitude clamp is observable at
  pole-adjacent latitudes, where the 120 m radius spans more degrees;
  `test_jitter_coordinates_pole_adjacent_points_stay_valid_latitudes` pins the
  result to valid latitudes on both sides of the pole with fixed seeds.
- `collect_building_locations` #3:
  `test_collect_building_locations_passes_the_requested_municipality_to_the_read`
  pins the `bfs_number` filter on the read itself.
