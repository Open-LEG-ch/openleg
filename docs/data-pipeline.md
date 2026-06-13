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
