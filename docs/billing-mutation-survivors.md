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
