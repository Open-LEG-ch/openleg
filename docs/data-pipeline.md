# Data Pipeline

OpenLEG combines public Swiss energy datasets into municipality profiles,
ranking pages, and API responses. The pipeline is deterministic and safe to run
against an empty development database.

## Sources

- BFE Anlagenregister: installed PV capacity by municipality and year.
- BFE Sonnendach: estimated municipal roof solar potential.
- BFS: municipality identifiers, population, canton, density, and comparison
  groups.
- ElCom: electricity tariff data used by public API and calculator flows.
- Energie Reporter: municipality energy transition indicators.

## Main Paths

- `public_data.py` fetches ElCom, Energie Reporter, and Sonnendach data.
- `scripts/load_pv_data.py` loads PV snapshots and the 10 year PV panel.
- `database.py` owns idempotent table creation and upsert helpers.
- `pv_ranking.py` computes utilization, peer comparisons, progress, and target
  guidance.
- `rangliste.py` renders ranking, progress, comparison, method, profile share,
  and badge routes.
- `api_public.py` exposes selected public read models.

## PV Import

Run from a configured development environment:

```bash
python scripts/load_pv_data.py
```

Expected effects:

- upsert municipality PV profile fields
- upsert 10 year PV panel rows
- keep unrelated municipality profile fields intact
- allow repeated imports without duplicate records

## SDAT Metering Data

Citizen smart meter data is a separate pipeline from the public open data
above. It never reaches the public API and never leaves the LEG.

The VNB delivers ebIX SDAT files. The E66 message (`ValidatedMeteredData`)
carries validated 15-minute readings; its E31 sibling is skipped on import.
`sdat_e66.py` parses one document into rows, `store/metering.py` stores them,
and `billing_readings.py` turns a period into frames for `billing_engine.py`.

### Canonical tables

| Table | Holds |
| --- | --- |
| `metering_points` | One row per metering point, with the mapping to an OpenLEG `building_id` and `community_id` |
| `metering_point_readings` | One row per (point, direction, interval) |
| `sdat_imports` | File ledger, one row per imported document |

`metering_point_readings` is the canonical readings table. The older
`meter_readings` table belongs to the manual `/meter-upload` path and is keyed
on `building_id`, not on a metering point. The two are not interchangeable.

### Ownership mapping

Billing is participant-based, not point-based. A metering point is billable
only when it carries both a `building_id` and a `community_id`, and when the
matching `community_members` row has `status = 'confirmed'`. One member can own
several points, for example a separate production meter, and one point can
carry both directions. Participants are therefore keyed on `building_id`.

An unmapped point or an unconfirmed member is a hard error, never a silent
skip: billing a period while a participant is missing would spread that
participant's share across everyone else.

### Units and directions

Volumes are kWh throughout. The readings table carries no unit column, so the
unit gate sits at the parse boundary: a document whose `MeasureUnit` is not
`KWH` is a hard parse error and never reaches storage. `direction` is
constrained to `consumption` or `production` and is part of the readings key,
because one physical point can be both at the same instant.

Each reading carries three channels: `total_kwh`, plus the VNB's own split into
`grid_kwh` and `community_kwh`. The VNB split is authoritative. The billing
engine reallocates totals only as an audit calculation. A missing VNB split or
any aggregate or participant-level difference blocks the draft period.

### Timezone semantics

`measured_at` is `TIMESTAMPTZ` and holds the interval **start** in UTC, because
the SDAT source is UTC and a naive local key would collide on the October DST
repeat. Periods are half-open, `[start, end)`, and are expressed in
`Europe/Zurich`. An inclusive end would bill the boundary interval twice.

### Data quality policy

`billing_readings.load_period_frames` refuses a period and reports every defect
it found, rather than the first, so one pass fixes a period:

- a point with no building, or a member who is not confirmed
- readings from a point that is not billable in this community
- a duplicated (point, direction, interval)
- a gap in a series that is otherwise present
- a timestamp off the quarter-hour grid
- a negative volume on any channel
- a resolution other than 15 minutes

A member with no production meter is not a gap. That participant simply has a
zero production column, which the engine needs in order to emit credits.

### Draft billing policy

`billing_runner.run_billing_period` is the production seam. The cron asks it to
process the previous complete `Europe/Zurich` calendar month. It creates only a
draft and applies these fail-closed rules:

- one explicit `billing_tariffs` row must cover the complete period
- internal and grid prices, network level, and distribution model are copied
  onto the draft; public H4 data and zero defaults are never substitutes
- incomplete readings or a difference from the VNB allocation persist nothing
- `(community_id, period_start, period_end)` is unique
- identical input fingerprints are retry no-ops; changed inputs require review
- the cron counts work only after `save_billing_period` returns a committed ID

Invoice issuance remains disabled until the LEG and its VNB approve tax, HKN,
tariff-class, rounding-tolerance, and operational cutoff rules.

## Public API Reads

The public API reads from normalized database helpers. It does not expose
citizen smart meter data.

Useful tables and helpers:

- `municipality_profiles`
- `municipality_pv_panel`
- `sonnendach_municipal`
- `elcom_tariffs`
- `get_pv_profiles`
- `get_municipality_pv_panel`
- `get_sonnendach_municipal`

## Regression Checks

Use:

```bash
pytest tests/test_pv_data.py tests/test_pv_panel_db.py tests/test_rangliste.py -q
pytest tests/test_api_public.py -q
```

Run the full gate before opening a PR:

```bash
pytest tests/ -q
ruff check .
ruff format --check .
```
