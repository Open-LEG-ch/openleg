# Billing mutation survivor classification

This record covers native `mutmut 3.7.0` runs for `billing_runner.py` and
`store/metering.py`, first for #382 and again for the direction cleanup in #436.

Run from the repository root:

```bash
python -m mutmut run
python -m mutmut results
```

## Result

| Run | Total | Killed | Survived |
| --- | ---: | ---: | ---: |
| Baseline for #382 | 569 | 333 | 236 |
| After #382 behavior tests | 569 | 559 | 10 |
| #436 fresh run | 643 | 626 | 17 |

The #436 run used an empty cache after the mutmut sandbox repair in #434. It
removed five direction survivors by deleting unsupported scalar and whitespace
normalization and by checking that logs name every unknown list value. One
direction survivor remains: it changes only the separator between those logged
values.

## Fresh classification for #676 (native run at `1bc638f`)

The runner battery path (`_battery_lines`), the public fingerprint's VNB
evidence fields, and the database-backed run path in `run_billing_period`
grew after the September waves and held 42 unclassified survivors. Behavior
tests in `tests/test_billing_runner.py` kill all of them:

- `_battery_lines` #2 and `run_billing_period` #52: the community battery
  read is pinned to the billed community
  (`test_battery_read_is_scoped_to_the_billed_community`,
  `test_operational_billing_stamps_the_recorded_vnb_evidence`).
- `_battery_lines` #6 and #7 are new equivalents: they rename the
  `community_id` key of the dict handed to `validate_battery_config`, but
  `db.get_battery` only returns the row whose `community_id` equals the
  queried community, so the reinforcement key can never change the validated
  result.
- `_battery_lines` #15: a battery participant row preceding a billed
  participant must not stop the scan
  (`test_a_battery_participant_row_never_hides_later_cost_shares`).
- `_battery_lines` #22-#24: the incomplete-configuration diagnostic is pinned
  word for word.
- `_battery_lines` #50-#56: the auditable snapshot field set, community,
  participant, and capacity are pinned in
  `test_battery_cost_share_lines_enter_the_draft_and_snapshot`.
- `_fingerprint` #51, #52, #54, #55: the VNB case id and source change the
  public fingerprint (`test_vnb_evidence_fields_change_the_public_run_fingerprint`);
  dropping or nulling either field collapses two different evidence sets onto
  one fingerprint.
- `run_billing_period` #23-#29: the validated calculated-values read is pinned
  to the community and period, and a run without evidence fails with the exact
  message.
- `run_billing_period` #36: the missing-evidence diagnostic is pinned word
  for word.
- `run_billing_period` #39-#43: the fetched evidence must be the one stamped
  into the frames.
- `run_billing_period` #204, #211, #213, #219-#225: the draft freezes the
  stamped evidence fingerprint and the prepared-by actor
  (`prepared_by` defaults to `"system"`).
- `previous_complete_month` #26 remains the documented equivalent above.

## Store metering classification for #680 (native run at `1bc638f`)

`get_sdat_veracity_flags` #6 and `record_sdat_veracity_flags` #31 mutated the
error diagnostics to `logger.error(None)`. The degrade-to-empty and
swallow-error tests now pin both messages, killing both mutants. The nine
SQL-casing equivalents from the #484 classification are unchanged.

Six of the other survivors are the unassigned-period behavior gaps tracked in
#435. The remaining ten were classified during #382 and do not change
application or PostgreSQL behavior.

## Evidence run for #495, #496 and #497

The ten-module scope (5,682 mutants) was run from an empty cache twice for
this wave: at `ec39e11` before the pins and at `50e5fd8` after them. Both
runs report 3,946 killed, 1,363 survived, 373 without coverage, strict score
69.45 percent. The counts are identical because every non-equivalent mutant
of these functions was already killed; the pins close the acceptance items
without moving the score. All ten mutants this record lists as intentional
equivalents still carry the identical diff and still survive.

The behavioral evidence for the equivalence classification is the full
selection suite (897 items) passing under each survivor, plus the pins added
for the ticket acceptance items: first-of-month and January-to-December
rollover through `now=` injection, and the absent-row and empty-list outcomes
of the metering-point queries. Each pin was shown red against a hand-applied
production break and green again after the revert. None of the ten survivors
changes a pinned outcome.

For the rollover mutant the equivalence is a calendar fact: at most two days
back from any month's first day lands on day 27 to 30 of the previous month,
never in a third month, so `replace(day=1)` yields the same date in every
year, leap years included. For the nine SQL case mutants, PostgreSQL folds
unquoted identifiers and treats keywords case-insensitively.

## Intentional equivalents

| Mutant | Mutation | Why behavior is unchanged |
| --- | --- | --- |
| `billing_runner.x_previous_complete_month__mutmut_26` | Subtract two days instead of one from the first day of the current month, then replace the day with 1. | Both dates are inside the previous calendar month, so both produce its first day. |
| `store.metering.x__canonical_directions__mutmut_8` | Replace `, ` with `XX, XX` between unknown direction values. | Every bad value remains in the error and the write still fails. Separator punctuation is not a repository contract. |
| `store.metering.x_get_metering_points__mutmut_10` | Lowercase `AND` in SQL. | PostgreSQL keywords are case-insensitive. |
| `store.metering.x_get_metering_point__mutmut_6` | Lowercase the complete `SELECT` statement. | PostgreSQL folds unquoted identifiers and treats keywords case-insensitively. |
| `store.metering.x_get_metering_point_readings__mutmut_10` | Lowercase the direction clause's `AND`. | PostgreSQL keywords are case-insensitive. |
| `store.metering.x_get_metering_point_readings__mutmut_16` | Lowercase the start-time clause's `AND`. | PostgreSQL keywords are case-insensitive. |
| `store.metering.x_get_metering_point_readings__mutmut_22` | Lowercase the end-time clause's `AND`. | PostgreSQL keywords are case-insensitive. |
| `store.metering.x_get_metering_point_readings__mutmut_28` | Lowercase `ORDER BY`, `DESC`, and `LIMIT`. | PostgreSQL keywords are case-insensitive. |
| `store.metering.x_get_sdat_import__mutmut_6` | Lowercase the complete `SELECT` statement. | PostgreSQL folds unquoted identifiers and treats keywords case-insensitively. |
| `store.metering.x_get_sdat_import_index__mutmut_3` | Lowercase the complete `SELECT` statement. | PostgreSQL folds unquoted identifiers and treats keywords case-insensitively. |
| `store.metering.x_get_sdat_import_index__mutmut_4` | Uppercase the selected identifiers and table name. | PostgreSQL folds unquoted identifiers to lowercase. |

Malformed SQL, invalid placeholders, changed parameters and defaults, removed
query clauses, changed reconciliation behavior, and suppressed log messages are
not classified as equivalent. The behavior tests kill those mutants.

## Policy form and display classification for #675 (native run at `a4c1f35`)

`billing_policy.py` held 71 survivors at the wave-3 baseline after the display
record was pinned. This wave's behavior tests in
`tests/test_billing_policy.py` kill 61 of them; ten are classified with
reachability probes (`/tmp` probe scripts confirmed the exact formats):

Killed through the form's own seam:

- every refused field names its exact German diagnostic; the two VAT
  branches, the nested fiscal-value messages and both payment-day sentence
  fragments are pinned word for word;
- the price upper boundary (1000 Rp./kWh) stays valid, one rate period above
  and a fifth decimal place are refused;
- VAT rates 0, `1e1`, `NaN`, 101, 100.01 and 0.001 are refused; 0.01, 1 and
  100 stay valid — this kills every `_vat_rate` domain mutant whose gate the
  pattern-masked `NaN` case alone could not reach;
- payment days accept both boundaries;
- `suggest_price_band` accepts a zero feed-in floor (killing the `<= 0` and
  `< 1` mutants) and refuses negative inputs, and its displays render two
  decimal places;
- `validate_persisted_policy` accepts a settlement fee exactly at its ceiling;
- a present-but-empty and a non-string VAT field read as empty.

Intentional equivalents:

| Mutant | Mutation | Why behavior is unchanged |
| --- | --- | --- |
| `_field` #3, #5 | Default a missing form key to `None` or drop the default. | `_field` normalizes every non-string to `""` through `isinstance`, so a `None` default reaches the parser exactly like the `""` default. |
| `_persisted_decimal` #5, #6 | Return `Decimal("nan")` or `Decimal("NAN")`. | Python's Decimal parser folds all three spellings to the same quiet, non-finite NaN; only `is_finite()` reads it. |
| `suggest_price_band` #35, #40, #46, #51 | Drop the `"f"` format spec or use `"F"`. | For every quantized plain decimal reachable as a floor or ceiling, `format(x)`, `format(x, "f")` and `format(x, "F")` produce identical text. |
| `validate_persisted_policy` #99 | Set `effective = None` in the type-fallback branch. | The flag is only read through `not effective`, which treats `None` and `False` alike. |
| `policy_fingerprint_values` #17 | Replace `value = value.isoformat()` with `None`. | `effective_from` is excluded from `FINGERPRINT_POLICY_FIELDS`, and every remaining projected field is explicitly stringified or a non-temporal value, so the temporal branch is unreachable. |
