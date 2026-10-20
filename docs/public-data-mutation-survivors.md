# Public data mutation survivor classification

These are historical results from the earlier #478 working branch. Mutant IDs and counts need a fresh unfiltered run at the current source revision. These classifications do not change native statuses or the automated gate.

Record for the #674 waves (parent #478) at historical revision `30b6b8e`.

These tables and the correction section record 31 historical survivor IDs:
25 in the tables plus 6 behavior-changing canton IDs in the correction
section. They do not establish a complete survivor count for that run. The
earlier 36-survivor summary was not reconciled with these tables; the five
other IDs are not recorded here. A fresh unfiltered native run is needed
before claiming a complete result.

## Intentional equivalents

Every listed mutant is only reachable through a default that the next
expression already absorbs or a key lookup that can never match. The kill
evidence lives in `tests/test_public_data.py` and
`tests/test_public_data_refresh_scope.py`.

### Absorbed `.get(key, default) or 0` defaults

The markup-wrapped defaults feed an `or` fallback that yields the same
value; a missing key reaches the same default either way.

| Mutant | Mutation |
| --- | --- |
| `compute_energy_transition_score` #10, #12, #28, #30, #46, #48, #59, #61, #70, #72 | Default one profile component to `None` or omit it. |
| `compute_leg_value_gap` #6, #8, #17, #19 | Default the grid fee or total to `None` or omit it. |

### Unreachable branches behind the fail-closed guard

| Mutant | Mutation | Why behavior is unchanged |
| --- | --- | --- |
| `compute_leg_value_gap` #88 | `total_rp > 0` → `total_rp >= 0`. | The `total_rp <= 0` guard returns before this branch, so the condition is decisive only when `total_rp > 0`. |
| `compute_leg_value_gap` #90 | The fallback `else 0` → `else 1`. | Same unreachable branch. |
| `refresh_municipality` #85 | Default the tariff category to `"XXXX"`. | A row without the key is skipped under both values and proceeds to the H4 rows that follow. |
| `refresh_canton` #44 | Default a bulk row's kanton to `"XXXX"`. | `"XXXX"` is as far from the target kanton as the empty string, and the row is skipped either way before any profile is written. |
| `refresh_canton` #2 | Default the kanton argument to `"zh"` instead of `"ZH"`. | `.strip().upper()` folds the default back to `"ZH"`. |

### Never-matching name constants

| Mutant | Mutation | Why behavior is unchanged |
| --- | --- | --- |
| `fetch_energie_reporter` #14, `fetch_sonnendach_municipal` #14 | Initialise `csv_url` to `""` instead of `None`. | Both values are falsy, and the next read is `if not csv_url:`. |
| `fetch_energie_reporter` #28, `fetch_sonnendach_municipal` #29, #71 | Default the resource format to `"XXXX"`. | `"XXXX"` is as non-CSV as the empty string; both skip the resource and fall back identically. |
| `fetch_sonnendach_municipal` #36 | Default the resource name to `"XXXX"`. | Neither default contains the municipal tokens. |

## Non-ZH canton correction

Six mutants previously listed as equivalents change the saved canton.

| Family | Canonical IDs | Observable difference |
| --- | --- | --- |
| Stored kanton | `public_data.x_refresh_municipality__mutmut_134`, `public_data.x_refresh_municipality__mutmut_138`, `public_data.x_refresh_municipality__mutmut_139` | Discards the stored kanton and resolves to `"ZH"`. |
| Reporter bulk kanton | `public_data.x_refresh_municipality__mutmut_209`, `public_data.x_refresh_municipality__mutmut_213`, `public_data.x_refresh_municipality__mutmut_214` | Discards the reporter bulk kanton and resolves to the existing or default `"ZH"`. |

When both bulk sources are unavailable and ElCom supplies a tariff, the
stored-canton mutants replace `"BE"` with `"ZH"` in the saved and returned
profile. For a new BE municipality supplied by Energie Reporter, the
reporter-canton mutants save and return `"ZH"`.

Commit `dd8090c0a68825a56c80aee47caf96017d2ce29f` adds
`test_refresh_retains_stored_non_zh_canton` and
`test_refresh_stores_reporter_canton_for_new_non_zh_profile`; the originals
pass, and each of the six exact generated-function targeted replays fails
one of the new tests. The fresh unfiltered native checkpoint
`3bf1aa479b9daa03a32e3dcf2f4dcd749540caad`, which preceded those tests,
still lists all six as survived. No later native kill count or score exists;
the later evidence is targeted replay only.

## Killed by behavior tests in the #674 waves

- `fetch_elcom_tariffs`: the LINDAS endpoint, SPARQL payload, headers,
  timeout and rendered municipality-year template; tariff records field by
  field including the degraded bindings; the empty results semantics; the
  exact operator log lines for success, failure and the degraded values.
- `fetch_energie_reporter` / `fetch_sonnendach_municipal`: the metadata and
  download call wiring, the municipal resource preference over each of the
  three tokens, the BFS column aliases with rows that must survive a skip,
  German and English column spellings, the utf-8 decode fallback, the
  format-absent fallback, the no-CSV warning and the parsed-count info lines.
- `compute_leg_value_gap`: the zero-gap dict for missing money, one-rappen
  boundaries, third-decimal fees, half-rappen totals and the full
  default-breakdown dict.
- `compute_energy_transition_score`: the depot that a doubled production
  ratio still caps at one and that a single MWh of consumption counts.
- `refresh_municipality`: the full orchestration contract (ElCom fetch call,
  save payload, per-source states, the exact saved profile with bulk values
  and stored-fallback rules, H4 selection tolerating an untyped tariff row,
  the H4-less zero value gap, cache invalidation call, exhausted-log
  diagnostics), the plain-fallback profile for a bulk outage, and the
  failing-invalidation line.
- `refresh_canton`: the seed and kanton defaults, the tariff year,
  per-source counters, the failure dictionary and log line, the kanton-less
  bulk row, and the `None`-kanton result.
