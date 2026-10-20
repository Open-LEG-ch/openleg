# Neighbor view mutation survivor classification

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

## Intentional equivalents

| Mutant | Mutation | Why behavior is unchanged |
| --- | --- | --- |
| `neighbor_view.x_jitter_coordinates__mutmut_5` | Treat a zero radius as a zero-distance jitter instead of returning early. | Both paths return the input coordinates. The mutated path multiplies its sampled distance by zero, so both deltas are zero for every finite coordinate and seed. |
| `neighbor_view.x_jitter_coordinates__mutmut_15` | Encode the seed with `"UTF-8"` instead of `"utf-8"`. | Python codec names are case-insensitive aliases of one registry entry; `str.encode("UTF-8")` is byte-identical to `str.encode("utf-8")` for every input, so the derived seed value and every jittered point are unchanged. |
| `neighbor_view.x_jitter_coordinates__mutmut_38` | Use earth radius `6378138.0` instead of `6378137.0`. | The change moves a point by less than 20 micrometres at the 120 m limit. It preserves the public contract: deterministic displacement inside the anonymity radius. Testing the private constant would assert implementation spelling. |
| `neighbor_view.x_jitter_coordinates__mutmut_49` | Guard `abs(denom) <= 1e-9` instead of `< 1e-9`. | The guard only differs when `abs(denom)` is exactly `1e-9`. `denom = 6378137.0 * cos(radians(lat))` is a float64 product; `1e-9` is not representable and no product of this form lands on it (at the pole the product is exactly `0.0`, and the adjacent floats are ~9.5e-10 and ~1.9e-9). The branch outcome is identical for every reachable input. |
| `neighbor_view.x_jitter_coordinates__mutmut_50` | Use the pole fallback whenever `abs(denom) < 1.000000001` instead of `< 1e-9`. | Both paths return deterministic, non-degenerate points inside the requested radius. They differ only in the longitude representation very close to a pole, which is not part of the public privacy contract. |
| `neighbor_view.x_jitter_coordinates__mutmut_73` | Wrap longitude with `(lon + delta - 180.0) % 360.0 - 180.0` instead of `(lon + delta + 180.0) % 360.0 - 180.0`. | Adding and subtracting 180 differ by exactly 360, and Python's float remainder is exact (IEEE `fmod`, then one exact `+360` adjustment), so both expressions produce bit-identical results for every finite longitude of geographic magnitude; the values only diverge when `|lon|` is large enough that `lon ± 180` rounds differently, far outside any coordinate this module receives. |

Malformed seeds, removed seed normalization, a positive radius that did not
jitter, a broken unseeded path, a stalled polar longitude, and a crashing pole
guard are not classified as equivalent. The behavior tests in
`tests/test_neighbor_view_privacy.py` kill those mutants.

## Provisional matching

Issue #499 started with seven survivors in `find_provisional_matches`: #24, #25,
and #30, #37, #38, #43, and #44. Behavior tests now pin inclusion at 150 metres,
exclusion beyond 150 metres, the profiles sent to the autarky calculation, and
the complete public summary. Native mutmut 3.7.0 verification killed all seven.

## Recheck after the BFS rewrite (#591) and #671

The #591 rewrite replaced distance matching with municipality matching, which
invalidated the old classifications and left eleven `find_provisional_matches`
survivors, three new `jitter_coordinates` survivors, and one
`collect_building_locations` survivor. #671 removed the orphaned `_coordinates`
helper whose six mutants had become the module's uncovered block.

One equivalent remains from the fresh run (`#73`, table above). Four older
jitter equivalents (`#15`, `#38`, `#49`, `#50`) are unchanged. The other
fifteen survivors are killed by behavior tests in
`tests/test_neighbor_view_privacy.py`:

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
