# Billing mutation survivor classification

This record covers the native `mutmut 3.7.0` run for `billing_runner.py` and
`store/metering.py` completed for issue #382.

Run from the repository root:

```bash
python -m mutmut run
python -m mutmut results
```

## Result

| Run | Total | Killed | Survived |
| --- | ---: | ---: | ---: |
| Baseline | 569 | 333 | 236 |
| After behavior tests | 569 | 559 | 10 |

The added tests cover billing orchestration, reconciliation, fingerprints,
provenance, metering writes, query behavior, defaults, and failure logging.
All remaining survivors are intentional equivalents. They do not change
application or PostgreSQL behavior.

## Intentional equivalents

| Mutant | Mutation | Why behavior is unchanged |
| --- | --- | --- |
| `billing_runner.x_previous_complete_month__mutmut_26` | Subtract two days instead of one from the first day of the current month, then replace the day with 1. | Both dates are inside the previous calendar month, so both produce its first day. |
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
