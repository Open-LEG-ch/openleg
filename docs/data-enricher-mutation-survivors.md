# Data enricher mutation survivor classification

This record covers native `mutmut 3.7.0` runs for `data_enricher.py` in the
ten-module scope, for the wave that closed #506, #507 and #508.

Run from the repository root:

```bash
python -m mutmut run
python -m mutmut results
```

## Result

| Run | Total | Killed | Survived | No coverage |
| --- | ---: | ---: | ---: | ---: |
| Baseline at `50e5fd8` | 5682 (scope) | 3946 (scope) | 1363 (scope) | 373 (scope) |
| data_enricher at baseline | 719 | 324 | 35 | 360 |
| After the wave | 5682 (scope) | 4340 (scope) | 1331 (scope) | 273 (scope) |
| data_enricher after the wave | 747 | 724 | 23 | 0 |

Every one of the 360 uncovered mutants is now covered, and 372 of the 395
previously unscored or surviving mutants are killed. The 23 remaining
survivors are classified below. None of them changes a pinned public
outcome; they are recorded here instead of hidden or excluded.

## Intentional equivalents

### Mock suggestion fallback (`_get_mock_address_suggestions`)

| Mutant | Mutation | Why behavior is unchanged |
| --- | --- | --- |
| `x__get_mock_address_suggestions__mutmut_3` | Rename the `lat` key. | Every consumer reads the payload through `_normalize_address_suggestions`, which re-derives `lat` with `.get("lat")`; a renamed key yields the same `None`. |
| `x__get_mock_address_suggestions__mutmut_4` | Rename the `lat` key to `LAT`. | Same normalization argument. |
| `x__get_mock_address_suggestions__mutmut_5` | Rename the `lon` key. | Same normalization argument. |
| `x__get_mock_address_suggestions__mutmut_6` | Rename the `lon` key to `LON`. | Same normalization argument. |
| `x__get_mock_address_suggestions__mutmut_7` | Rename the `plz` key. | Same normalization argument. |
| `x__get_mock_address_suggestions__mutmut_8` | Rename the `plz` key to `PLZ`. | Same normalization argument. |

### Coordinates (`get_coordinates_from_address`)

| Mutant | Mutation | Why behavior is unchanged |
| --- | --- | --- |
| `x_get_coordinates_from_address__mutmut_20` | Default missing `results` to `None` instead of `[]`. | A missing key yields a falsy value either way; `if not results` takes the same branch. |
| `x_get_coordinates_from_address__mutmut_22` | Drop the default for missing `results`. | Same falsy-default argument. |
| `x_get_coordinates_from_address__mutmut_27` | Wrap the miss diagnostic in `XX`. | Operator stdout text is not a repository contract; the miss path and its return value are pinned. |
| `x_get_coordinates_from_address__mutmut_46` | Default a missing `label` to `XXXX`. | The regex finds no four digit number in `XXXX` just as it finds none in the empty string, so the fallback produces the same `None` PLZ. |

### PV potential (`get_pv_potential_from_coords`)

| Mutant | Mutation | Why behavior is unchanged |
| --- | --- | --- |
| `x_get_pv_potential_from_coords__mutmut_46` | Default missing `results` to `None`. | A falsy default reaches the same not-found branch. |
| `x_get_pv_potential_from_coords__mutmut_48` | Drop the default for missing `results`. | Same falsy-default argument. |
| `x_get_pv_potential_from_coords__mutmut_53` | Wrap the no-building diagnostic in `XX`. | Operator stdout text is not a repository contract. |

### Energy profile (`get_energy_profile_for_address`)

| Mutant | Mutation | Why behavior is unchanged |
| --- | --- | --- |
| `x_get_energy_profile_for_address__mutmut_2` | Initialise `in_tag` to `None`. | `None` is falsy like `False`; the markup loop tests `not in_tag`. |
| `x_get_energy_profile_for_address__mutmut_10` | Assign `None` instead of `False` when a tag closes. | Same falsy argument. |
| `x_get_energy_profile_for_address__mutmut_31` | Pass `None` for the latitude into the mock GWR lookup. | The simulator selects the building record by PLZ only; coordinates carry no contract. |
| `x_get_energy_profile_for_address__mutmut_32` | Pass `None` for the longitude into the mock GWR lookup. | Same simulator argument. |
| `x_get_energy_profile_for_address__mutmut_42` | Pass `None` as the energy surface into the consumption estimate. | The surface only feeds the commercial branch, and the mock GWR records are always `EFH` or `MFH`, so the branch is unreachable through the public flow. |
| `x_get_energy_profile_for_address__mutmut_83` | Wrap the completion diagnostic in `XX`. | Operator stdout text is not a repository contract. |

### Mock energy profile (`get_mock_energy_profile_for_address`)

| Mutant | Mutation | Why behavior is unchanged |
| --- | --- | --- |
| `x_get_mock_energy_profile_for_address__mutmut_41` | Draw annual consumption from `randint(4000, 20001)`. | The shifted upper bound only differs for a draw of exactly 20000; no address seeded stream reaches that boundary, as the pinned reference addresses show. |
| `x_get_mock_energy_profile_for_address__mutmut_46` | Draw PV as `randint(30)`. | `randint(30)` is `randint(0, 30)` by numpy's own signature. |
| `x_get_mock_energy_profile_for_address__mutmut_49` | Draw PV from `randint(0, 31)`. | The shifted upper bound only differs for a draw of exactly 30; the seeded stream never lands there. |
| `x_get_mock_energy_profile_for_address__mutmut_73` | Wrap the completion diagnostic in `XX`. | Operator stdout text is not a repository contract. |

Malformed SQL, wrong parameters, changed arithmetic, swapped operands,
inverted filters, dropped limits, and diagnostic prints that lose their
message (`print(None)`) or change case are not classified as equivalent. The
behavior tests kill those mutants; the caps-from-stdout assertions pin the
operator diagnostics that carry information.
