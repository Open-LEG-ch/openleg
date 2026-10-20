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
| data_enricher at baseline `50e5fd8` | 637 | 242 | 35 | 360 |
| data_enricher after the wave at `465e0b9` | 637 | 614 | 23 | 0 |
| Scope at baseline `50e5fd8` | 5682 | 3946 | 1363 | 373 |
| Scope after the wave at `465e0b9` | 5682 | 4318 | 1351 | 13 |

The scope's strict score moved from 69.45 to 75.99 percent. Every one of the
360 previously uncovered mutants of data_enricher is now covered, and 372 of
the 395 previously unscored or surviving mutants are killed. The survivors
then remaining (23 of them) are recorded below, reclassified wave by wave:
the fresh classification sits in "Fresh kills for #677" and the equivalence
tables carry the mutants that still survive at HEAD. None of them changes a
pinned public outcome; they are recorded here instead of hidden or excluded.
The 13 uncovered mutants left in the scope at that time belonged to
`public_data` and were outside this wave's tickets.

## Intentional equivalents

Position for this wave (#677 and the fresh run, below): operator diagnostics
on stdout ARE observable behavior. The earlier entries that called diagnostic
wording un-pinned are superseded — `tests/test_conversion_paths.py` now pins
the exact diagnostic lines, and the affected mutants are killed. What remains
below are mutants that no public seam can distinguish.

### Address suggestions fallback (`_get_mock_address_suggestions`)

None remain. The offline fallback shape is pinned directly
(`test_the_offline_mock_suggestions_keep_the_suggestion_shape`), killing the
six key-rename mutants the earlier wave had classified equivalent through the
normalization argument.

### Coordinates (`get_coordinates_from_address`)

| Mutant | Mutation | Why behavior is unchanged |
| --- | --- | --- |
| `x_get_coordinates_from_address__mutmut_20` | Default missing `results` to `None`. | The value only feeds `if not results`, which takes the same branch for `None` and `[]`. |
| `x_get_coordinates_from_address__mutmut_22` | Drop the default for missing `results`. | Same falsy-default argument. |
| `x_get_coordinates_from_address__mutmut_46` | Default a missing `label` to `XXXX`. | The regex looks for four digits; neither default carries them, so both produce a `None` PLZ. |

### PV potential (`get_pv_potential_from_coords`)

| Mutant | Mutation | Why behavior is unchanged |
| --- | --- | --- |
| `x_get_pv_potential_from_coords__mutmut_50` | Default missing identify `results` to `None`. | The value only feeds `if not results`; the same not-found branch runs. |
| `x_get_pv_potential_from_coords__mutmut_52` | Drop the default for missing identify `results`. | Same falsy-default argument. |
| `x_get_pv_potential_from_coords__mutmut_170` | Default a missing `stromertrag` to `None`. | The sum feeds the value through `or 0`, which absorbs `None` and `0` alike. |
| `x_get_pv_potential_from_coords__mutmut_172` | Drop the default for missing `stromertrag`. | Same `or 0` absorption. |

### Municipality (`get_municipality_from_coords`)

| Mutant | Mutation | Why behavior is unchanged |
| --- | --- | --- |
| `x_get_municipality_from_coords__mutmut_74` | Default `current.get("attributes", {})` missing to `None`. | Only a row passing the `is_current_jahr` filter becomes `current`, and that filter reads the item's `attributes` — so the key is always present and truthy at the second read. |
| `x_get_municipality_from_coords__mutmut_76` | Drop the same default. | Same reachability argument. |

### Energy profile (`get_energy_profile_for_address`)

| Mutant | Mutation | Why behavior is unchanged |
| --- | --- | --- |
| `x_get_energy_profile_for_address__mutmut_2` | Initialise `in_tag` to `None`. | `None` is falsy like `False`; the markup loop only tests `not in_tag`. |
| `x_get_energy_profile_for_address__mutmut_10` | Assign `None` instead of `False` when a tag closes. | Same falsy argument. |

### Mock energy profile (`get_mock_energy_profile_for_address`)

| Mutant | Mutation | Why behavior is unchanged |
| --- | --- | --- |
| `x_get_mock_energy_profile_for_address__mutmut_46` | Draw PV as `randint(30)`. | `randint(30)` is `randint(0, 30)` under numpy's signature; the interval is identical. |

## Fresh kills for #677 (native evidence for #478)

Native mutmut 3.7.0 reference run at `a4c1f35` from an empty cache scores the
module at 875 mutants with 759 killed and 116 survived. This wave's behavior
tests in `tests/test_conversion_paths.py` kill 104 of the 116, leaving the
twelve equivalents above:

- `get_municipality_from_coords`: the geoadmin identify query is pinned field
  by field, including URL and timeout; sparse answers degrade silently and
  exactly (no operator error text for a no-match); the missing-name and
  missing-BFS refusals are discriminated separately from the upstream-failure
  diagnostic; the label fallback name is pinned; a 150-character name is
  truncated to its first 120 characters; the operator error line is pinned.
- `get_pv_potential_from_coords`: the missing-building, missing-identity,
  incomplete-facet, no-progress and page-limit diagnostics are pinned word for
  word; the zero-return values on each failure branch are pinned; a mixed
  facet without solar values contributes zero and still renders the success
  line; a page response without results degrades to zero solar data with the
  success line.
- `get_coordinates_from_address`: the not-found diagnostic is pinned exactly.
- `get_energy_profile_for_address`: the municipality lookup receives the
  coordinates the walk actually found; the GWR simulator receives coordinates,
  PLZ and their values; a service building's consumption is derived from its
  energy surface; both the no-municipality and completion diagnostics are
  pinned exactly.
- `get_mock_energy_profile_for_address`: the completion diagnostic is pinned
  exactly, and the seeded reference-address draws keep pinning the
  `randint(4000, 20001)` and `randint(0, 31)` boundary shifts the first wave
  killed.
- `_plz_in_ranges`: inclusive PLZ boundaries at 1000, 9999 and inside a
  custom range.
- `_get_mock_address_suggestions`: the offline suggestion shape is pinned.
