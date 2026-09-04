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
#10, #15, #23, #38, #49, #50, #51.

## Intentional equivalents

| Mutant | Mutation | Why behavior is unchanged |
| --- | --- | --- |
| `neighbor_view.x_jitter_coordinates__mutmut_5` | Treat a zero radius as a zero-distance jitter instead of returning early. | Both paths return the input coordinates. The mutated path multiplies its sampled distance by zero, so both deltas are zero for every finite coordinate and seed. |
| `neighbor_view.x_jitter_coordinates__mutmut_15` | Encode the seed with `"UTF-8"` instead of `"utf-8"`. | Python codec names are case-insensitive aliases of one registry entry; `str.encode("UTF-8")` is byte-identical to `str.encode("utf-8")` for every input, so the derived seed value and every jittered point are unchanged. |
| `neighbor_view.x_jitter_coordinates__mutmut_38` | Use earth radius `6378138.0` instead of `6378137.0`. | The change moves a point by less than 20 micrometres at the 120 m limit. It preserves the public contract: deterministic displacement inside the anonymity radius. Testing the private constant would assert implementation spelling. |
| `neighbor_view.x_jitter_coordinates__mutmut_49` | Guard `abs(denom) <= 1e-9` instead of `< 1e-9`. | The guard only differs when `abs(denom)` is exactly `1e-9`. `denom = 6378137.0 * cos(radians(lat))` is a float64 product; `1e-9` is not representable and no product of this form lands on it (at the pole the product is exactly `0.0`, and the adjacent floats are ~9.5e-10 and ~1.9e-9). The branch outcome is identical for every reachable input. |
| `neighbor_view.x_jitter_coordinates__mutmut_50` | Use the pole fallback whenever `abs(denom) < 1.000000001` instead of `< 1e-9`. | Both paths return deterministic, non-degenerate points inside the requested radius. They differ only in the longitude representation very close to a pole, which is not part of the public privacy contract. |

Malformed seeds, removed seed normalization, a positive radius that did not
jitter, a broken unseeded path, a stalled polar longitude, and a crashing pole
guard are not classified as equivalent. The behavior tests in
`tests/test_neighbor_view_privacy.py` kill those mutants.

## Provisional matching

Issue #499 started with seven survivors in `find_provisional_matches`: #24, #25,
#30, #37, #38, #43, and #44. Behavior tests now pin inclusion at 150 metres,
exclusion beyond 150 metres, the profiles sent to the autarky calculation, and
the complete public summary. Native mutmut 3.7.0 verification killed all seven.
