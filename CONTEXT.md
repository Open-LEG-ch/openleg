# Context

Domain vocabulary and module/seam names for OpenLEG. CLAUDE.md and AGENTS.md
point here. Use these names in code, tests, commits, and issues so the same
concept never travels under two labels.

## Domain Terms

| Term | Meaning |
| --- | --- |
| LEG | Lokale Elektrizitätsgemeinschaft. Neighbours who share locally produced electricity over the public grid at a reduced network fee. The product's reason to exist. |
| ZEV | Zusammenschluss zum Eigenverbrauch. The older, building-bound model. A LEG is the wider successor and reaches across parcels. |
| VNB | Verteilnetzbetreiber. The distribution grid operator that meters the LEG, applies the network discount, and bills the residual grid supply. |
| EVU | Elektrizitätsversorgungsunternehmen. The utility. Often the same company as the VNB; kept apart because the roles differ. |
| ElCom | Eidgenössische Elektrizitätskommission. Publishes the yearly tariff dataset per municipality and category. |
| BFS | Bundesamt für Statistik. `bfs_number` is the municipality primary key across the whole codebase. |
| BFE | Bundesamt für Energie. Source of the Anlagenregister (installed PV) and Sonnendach (roof solar potential). |
| Messpunkt | Metering point. Identified by a Swiss `metering_point_id`; one per participant connection. |
| SDAT | Swiss Data Exchange for the energy market. The ebIX-based XML format the VNB delivers meter data in. |
| E66 | The SDAT `ValidatedMeteredData` message carrying validated 15-minute readings. E31 is its sibling message and is skipped on import. |
| Datahub | Swisseldex Datahub, the FTPS drop the VNB writes SDAT files into. |
| Netzentgelt | Grid fee per kWh. A LEG earns a discount on it for energy consumed inside the community. |
| Rangliste | The public ranking of municipalities by solar utilization. |
| Territory | A tenant slug resolved from the hostname (`dietikon.openleg.ch` to `dietikon`). Also called `city_id` in older call sites. |
| Gemeinde | Municipality. Its public page is the Gemeindeprofil. |
| LEA | The AI agent persona served through the OpenClaw gateway. |
| Neighbour view | The resident-visible map and match summary: jittered coordinates, no identities, consent-gated. |

## Seams

A seam is a named indirection that tests replace and modules resolve at call
time. Two matter:

- **`database.get_connection`** is the single connection seam. `database.py`
  owns the pool, the schema, and this contextmanager. Every store module
  resolves it lazily so that patching `database.get_connection` reaches all of
  them and no import cycle forms:

  ```python
  def _get_connection():
      import database

      return database.get_connection()
  ```

  Store functions are re-exported at the bottom of `database.py`, so
  `import database as db; db.get_metering_points()` keeps working. Call storage
  through `db.` in application code; import `store.<domain>` directly only
  inside that domain's own tests.

- **`tenant.get_tenant_config`** is the tenancy seam. A `before_request` hook
  resolves the hostname to a territory and puts the config on `g.tenant`.
  Templates render through `render_city_template`, which prefers
  `templates/cities/<territory>/<name>.html` and falls back to the shared file.

## Module Names

Storage lives in `store/`, one module per self-contained domain:

| Module | Owns |
| --- | --- |
| `store/building` | Building registrations, consent-gated building reads, dashboard building data |
| `store/cluster` | Provisional cluster assignments and cluster metadata |
| `store/ranking` | PV snapshots, the ten-year panel, Rangliste read models |
| `store/profile` | Gemeindeprofil: ElCom tariffs, profiles, Sonnendach |
| `store/billing` | LEG communities, billing periods, versioned billing policies, atomic invoice approval snapshots |
| `store/email_queue` | Outbound mail queue |
| `store/utility` | EVU/VNB utility clients |
| `store/metering` | Messpunkte, 15-minute E66 readings, SDAT import ledger |
| `store/meter` | Per-building meter readings from the upload path |
| `store/registry` | LEG registry entries and verification |
| `store/tenant` | White-label tenant configs |
| `store/token` | Auth and claim tokens |
| `store/analytics` | Event log and the aggregate counts the dashboards read |
| `store/consent` | The consent record a resident gives and can revoke |
| `store/document` | Generated LEG documents and their signing status |
| `store/ops` | LEA job reports and operational snapshots |
| `store/access_token` | Hashed, single-use magic-link tokens, dashboard and municipality |

The extraction is finished. `database.py` owns the connection pool,
`get_connection`, the schema call, and the re-export block, and nothing else.
New storage code for a cohesive domain gets its own module in `store/`, never
an append to `database.py`.

Domain logic sits above storage and stays free of SQL:

| Module | Owns |
| --- | --- |
| `billing_engine.py` | `allocate_energy`, `compute_network_discount`, `generate_billing_summary` |
| `billing_policy.py` | Billing policy validation, option labels, the no-legal-advice disclaimer |
| `billing_runner.py` | Fail-closed draft run; resolves and fingerprints the complete effective policy |
| `billing_approval.py` | Fail-closed approval validation; immutable invoice snapshots from the stored policy/provenance seam |
| `pv_ranking.py`, `ranking.py` | Utilization, peer comparison, progress |
| `municipality_profile.py` | Tariff, solar, and value-gap assembly |
| `formation_wizard.py`, `document_generator.py` | LEG formation and documents |
| `sdat_e66.py`, `sdat_datahub.py`, `meter_data.py` | Meter data parsing and retrieval |
| `ml_models.py`, `data_enricher.py` | Clustering and enrichment |
| `neighbor_view.py` | Neighbour read policy: anonymity radius, jittered map locations, provisional match summary |
| `access_token.py` | Magic-link access policy: token format, hashing, expiry bounds, access URLs |

## Naming Rules

- `building_id` identifies a registration; `metering_point_id` identifies a
  Messpunkt; `community_id` identifies a LEG. They are not interchangeable.
- `bfs_number` is always the integer municipality key, never the name.
- Energy is `kwh`, power is `kwp`, tariffs are `rp_kwh` (Rappen per kWh), money
  is `chf`. Suffix the unit rather than commenting it.
- Timestamps are stored in UTC. The `measured_at` column on readings is UTC even
  though SDAT files arrive in local time.
- German identifiers stay ASCII; German prose uses real umlauts and `ss`.

## Related Docs

- Architecture and extraction order: `docs/architecture.md`
- Data pipeline and import commands: `docs/data-pipeline.md`
- Repository boundary: `docs/repo-boundary.md`
- Agent execution contract: `docs/codex-execution.md`
