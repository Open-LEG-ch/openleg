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
