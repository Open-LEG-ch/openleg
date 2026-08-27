# Data Pipeline

OpenLEG combines public Swiss energy datasets into municipality read models and
API responses. The separately deployed public website renders ranking and profile
pages from those APIs. The pipeline is deterministic and safe to run against an
empty development database.

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

## SDAT Retrieval (Swisseldex Datahub)

`sdat_datahub.py` fetches the SDAT files a VNB drops into our Datahub outbox at
`ftpes://datahub.swisseldex.ch` (FTP with explicit TLS). It only retrieves
files; parsing and ingestion belong to `sdat_e66.py` and `store/metering.py`,
documented in the sections below.

Set the credentials in `.env` first (template: `.env.example`):

```ini
SWISSELDEX_FTPS_USER=
SWISSELDEX_FTPS_PASSWORD=
SWISSELDEX_FTPS_REMOTE_DIR=/
SWISSELDEX_SDAT_DIR=data/sdat
```

Run manually:

```bash
python scripts/fetch_sdat.py --list          # zeigt nur an, was geladen würde
python scripts/fetch_sdat.py                 # lädt alle neuen Dateien
python scripts/fetch_sdat.py --recursive     # auch Unterverzeichnisse
python scripts/fetch_sdat.py --since-days 7  # nur die letzte Woche
```

Behaviour worth knowing:

- Downloads land in `SWISSELDEX_SDAT_DIR` (default `data/sdat`, gitignored
  because the files hold real citizen metering data).
- Files already present locally are skipped, so repeated runs are cheap.
  `--force` re-downloads them.
- Transfers are atomic: a failed download leaves no partial file behind.
- Remote files stay on the Datahub unless you pass `--delete-remote`.
- The connector resumes the control-channel TLS session on the data channel,
  which managed FTPS endpoints usually require.

## SDAT Metering Data

Citizen smart meter data is a separate pipeline from the public open data
above. It never reaches the public API and never leaves the LEG.

The VNB delivers load curves as ebIX SDAT XML. Two document types arrive in the
same directory:

- **E66** (`ValidatedMeteredData_16`): validated 15-minute values per metering
  point. This is the source for LEG billing.
- **E31** (`AggregatedMeteredData_13`): aggregates at LEG level, without a
  metering point. The import skips them.

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

### Structure of an E66 file

One block per combination of metering point, direction, and product channel.
Each pair of metering point and direction carries three channels:

| Product code | Channel | Column |
| --- | --- | --- |
| `8716867000030` (ebIXCode) | Total energy | `total_kwh` |
| `2404050010124` (VSENationalCode) | Grid share | `grid_kwh` |
| `2404050010123` (VSENationalCode) | LEG share | `community_kwh` |

`total = grid + community` holds. The LEG channel balances across the
community: what production points feed into the LEG, consumption points draw
from it.

Observations carry no timestamp of their own. It follows from the interval
start of the block plus `(Sequence - 1) * resolution` and marks the **start**
of the interval. Everything is UTC, hence `TIMESTAMPTZ`.

### Ownership mapping

Billing is participant-based, not point-based. A metering point is billable
only when it carries both a `building_id` and a `community_id`, and when the
matching `community_members` row has `status = 'confirmed'`. One member can own
several points, for example a separate production meter, and one point can
carry both directions. Participants are therefore keyed on `building_id`.

An unmapped point or an unconfirmed member is a hard error, never a silent
skip: billing a period while a participant is missing would spread that
participant's share across everyone else.

Billing also checks imported, unassigned points before it builds a draft. It
derives the relevant public VNB LEG identifiers from in-period readings of
points already assigned to the requested OpenLEG community, then reports only
the unassigned metering point IDs in that VNB scope. It returns no readings or
participant details and returns no identifiers from another VNB LEG scope.

### Declared directions

`metering_points.expected_directions` declares which series a point is expected
to deliver: `consumption`, `production`, or both, stored as a canonical array
in that order. Billing reads the declaration through
`get_community_metering_points` and treats a point without it as a hard error,
because a missing series cannot otherwise be told apart from an absent meter.

The operator declares the directions in the participant list CSV, in the
optional `expected_directions` column, pipe-separated
(`consumption|production`). `scripts/import_metering_points.py` canonicalizes
the values, rejects unknown ones, and never blanks a stored declaration with an
empty field.

Points registered before the column existed carry NULL. Those legacy points
must be enriched through the participant list before billing, because the
billing gate refuses a point whose declaration is missing.

### Units and directions

Volumes are kWh throughout. The readings table carries no unit column, so the
unit gate sits at the parse boundary: a document whose `MeasureUnit` is not
`KWH` is a hard parse error and never reaches storage. `direction` is
constrained to `consumption` or `production` and is part of the readings key,
because one physical point can be both at the same instant. The full key is
therefore `(metering_point_id, direction, measured_at)`.

The VNB split into `grid_kwh` and `community_kwh` is authoritative. The billing
engine reallocates totals only as an audit calculation. A missing VNB split or
any aggregate or participant-level difference blocks the draft period.

### Timezone semantics

`measured_at` is `TIMESTAMPTZ` and holds the interval **start** in UTC, because
the SDAT source is UTC and a naive local key would collide on the October DST
repeat. Periods are half-open, `[start, end)`, and are expressed in
`Europe/Zurich`. An inclusive end would bill the boundary interval twice.

The VNB anchors its reporting window to local midnight (22:00Z in summer,
23:00Z in winter). A five-day window is 480 quarter-hours only outside the
switch: it holds 476 across the spring change and 484 across the autumn one,
because the local day loses or gains an hour. Never assume a fixed count.

### Delivery and corrections

Daily deliveries cover five days and overlap by four, so later files correct
earlier values. The import therefore upserts with last write wins.

`Condition` flags individual values but does not reliably predict corrections.
Do not rely on it.

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

The fingerprint covers the complete period provenance, canonical production and
consumption frames, participant IDs, VNB reference, tariff, generated summary,
and reconciliation. Its public runner contract pins a stable SHA-256 vector, so
removing any of those inputs is observable before an immutable draft is reused.

Invoice issuance remains disabled until the LEG and its VNB approve tax, HKN,
tariff-class, rounding-tolerance, and operational cutoff rules.

### Running the import

```bash
python scripts/import_sdat.py data/<gemeinde> --dry-run
python scripts/import_sdat.py data/<gemeinde>
python scripts/import_metering_points.py data/<gemeinde>/teilnehmer.csv
```

The import skips documents it already read, tracked in `sdat_imports`.
`--force` reads them again. `--dry-run` needs no database.

### Skipping what is already imported

A municipality directory grows with every delivery and never shrinks, so a run
must avoid fully parsing settled files. The importer decides identity from the
document header before parsing:

| Key | Cost per settled file |
| --- | --- |
| document id, from the first 16 KB | one small read, no full parse |

`get_sdat_import_index` replaces one ledger query per file with a single query
per run. Only a genuinely new E66 is read in full and parsed. On
the test fixture a full parse is 0.736 ms against 0.0003 ms for a head scan, and
the gap widens with file size.

Every readable but uncertain case does the full work instead of skipping: a
head that yields no document id, a ledger read that failed, and `--force`.
Skipping a delivery the ledger does not know about would lose it silently, so
uncertainty costs time rather than data. If the head itself is unreadable, the
import reports the file as failed and leaves it in place.

Two consequences worth knowing:

- The run reports skipped files separately (`N bereits importiert`), so a run
  that does almost nothing still says what it saw.
- File names are reporting metadata, never identity. Two documents with the
  same file name are distinguished by their document ids.

### Disk use after the import

Once a document's rows are in the database, the import packs its XML back into
`<name>.xml.gz` and removes the plain file. SDAT XML is highly repetitive, so
this costs around a tenth of the space. The unpacked files are the bulk of
`data/` and nothing reads them again after the import.

A file is packed once it is settled, meaning nothing will read it again:

- an E66 whose rows the run just wrote
- an E66 already recorded in `sdat_imports`
- an E31 sibling, which the import skips by design and which arrives with every
  delivery

A dry run, a parse failure, and a file that is neither E66 nor E31 leave the
file plain. A failed parse or an unrecognised document may be a delivery problem
someone has to read, and that file is the only local copy of it. "Not an E66" is
therefore not treated as "an E31": the importer tests for E31 explicitly.

The archive is written to a `.part` file and read back before it is published
without overwriting an existing archive. Only then is the original deleted. A
failed pack or name conflict costs disk space, never data, and reports a warning
without failing the run. `--no-compress` turns it off.

`import_sdat.py` reads `*.xml.gz` directly, so packing never hides a file from
the importer and `--force` still works afterwards. That is also why
`sdat_pipeline.sh` no longer unpacks the municipality directory: a settled file
stays packed and is never opened again. Unpacking it for the importer to repack
it was work that grew with the archive and produced nothing.

A directory that predates packing holds plain, already imported XML. Those files
are packed once, on the skip path, without being parsed.

Metering point IDs are personal data as soon as they link to the register.
Output and logs show only the last six digits. Real exports live under `data/`
and stay unversioned.

### Regression checks

```bash
pytest tests/test_sdat_e66.py tests/test_store_metering.py \
       tests/test_metering_schema.py tests/test_import_sdat_script.py \
       tests/test_import_sdat_compression.py tests/test_import_sdat_skip.py -q
```

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
