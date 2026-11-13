# Context

Domain vocabulary and module/seam names for OpenLEG. The public engineering
contract points here. Use these names in code, tests, commits, and issues so the same
concept never travels under two labels.

## Domain Terms

| Term | Meaning |
| --- | --- |
| LEG | Lokale Elektrizitätsgemeinschaft. Neighbours who share locally produced electricity over the public grid at a reduced network fee. The product's reason to exist. |
| ZEV | Zusammenschluss zum Eigenverbrauch. The older, building-bound model. A LEG is the wider successor and reaches across parcels. |
| VNB | Verteilnetzbetreiber. The distribution grid operator that meters the LEG, applies the network discount, and bills the residual grid supply. |
| VNB preview | Authorization, validation and conflict checks for a VNB-bound request without business writes. Returns intended action, canonical payload fingerprint and adapter recipient; usage accounting still applies. |
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
| Verified interest | A confirmed email's interest in a LEG within one BFS municipality, counted once across the address-check and coverage-request journeys. |
| Demodorf | The fictional village Sonnwil that visitors open without an account to try OpenLEG (fr: village de démonstration, en: demo village, it: villaggio demo). Each visit gets its own disposable sandbox workspace; code and URLs keep the name `sandbox`. Formerly called Übungsdorf. |
| Coverage request | An interest submission whose address could not be resolved. It can contribute to municipality demand after email confirmation. |

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
  inside that domain's own tests, except for billing operations below.

  **Billing-only exception:** `billing_approval.approve_billing_period` owns
  its transaction through `database.get_connection` and passes the cursor to
  `store.billing` helpers. Those helpers only read, lock, and write rows;
  approval validation, replay, numbering, snapshots, and outcomes belong to
  the operation. Locks remain community first, then period. No session object
  is introduced. Callers import the operation directly; its `database` export
  is removed with the caller migration. Other billing operations keep their
  existing exports until their own migrations.

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
| `store/billing` | LEG communities, billing periods, versioned billing policies, invoice snapshot persistence and approval row locks, append-only invoice lifecycle audit, atomic bank import and reviewed-payment acceptance |
| `store/email_queue` | Outbound mail queue |
| `store/utility` | EVU/VNB utility clients |
| `store/metering` | Messpunkte, 15-minute E66 readings, SDAT import ledger |
| `store/calculated_values` | Tenant-scoped VNB-calculated LEG allocations, validation outcomes, replay fingerprints, and original evidence |
| `store/meter` | Per-building meter readings from the upload path |
| `store/registry` | LEG registry entries and verification |
| `store/tenant` | White-label tenant configs |
| `store/token` | Auth and claim tokens |
| `store/interest` | Verified municipality-interest counts, recipients, summaries, and raw operator exports |
| `store/analytics` | Event log and the aggregate counts the dashboards read |
| `store/consent` | The consent record a resident gives and can revoke |
| `store/document` | Generated LEG documents and their signing status |
| `store/ops` | LEA job reports and operational snapshots |
| `store/oauth` | OAuth authorization codes, grant persistence and token introspection |
| `store/access_token` | Hashed, single-use magic-link tokens, dashboard and municipality |
| `store/advisories` | Immutable reviewed source revisions, source-review policy/history and private equipment acknowledgements |

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
| `billing_approval.py` | Approval transaction, validation, replay, invoice numbering and immutable snapshots from the stored policy/provenance seam |
| `billing_lifecycle.py` | Allowed invoice state transitions and shared member/admin status labels |
| `payment_reconciliation.py` | ISO 20022 camt.053/.054 parsing, reference selection and shared bank-payment eligibility |
| `pv_ranking.py`, `ranking.py` | Utilization, peer comparison, progress |
| `public_data.py` | Gemeindeprofil refresh outcomes, source-field preservation, tariff/solar merge, and value-gap assembly |
| `municipality_profile.py` | Gemeindeprofil presentation helpers |
| `formation_wizard.py`, `document_generator.py` | LEG formation and documents |
| `formation_guide.py` | Public formation-guide FAQ context and matching FAQPage data |
| `sdat_e66.py`, `sdat_datahub.py`, `meter_data.py` | Meter data parsing and retrieval |
| `data_enricher.py` | Address-suggestion and profile outcomes; live/mock selection, normalization, and fallback cause |
| `ml_models.py` | Clustering algorithms and generated load profiles |
| `clustering_run.py` | Complete clustering-run orchestration and persistence outcomes |
| `neighbor_view.py` | Neighbour read policy: anonymity radius, jittered map locations, provisional match summary |
| `access_token.py` | Magic-link access policy: token format, hashing, expiry bounds, access URLs |
| `interest_intake.py` | Coverage-request validation and confirmation-mail submission |
| `interest_confirmation.py` | Building and coverage confirmation outcomes, then mail scheduling, deferred clustering, and municipality notification |
| `advisories.py` | Exact reviewed device identity, conservative version profiles, source-review freshness and private notice presentation |

## Naming Rules

- A **sandbox workspace** is one disposable copy of the fictional village. It
  owns a dedicated product process and scratch database; it is not a Territory
  or a LEG. `sandbox_runtime` owns its provisioning and HTTP entry point.
  `sandbox_network` owns the worker's Unix-only socket boundary.

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

## Operator actor attribution

An **operator actor** identifies the initiating API credential separately from
its building authorization principal (`created_by`). Its kind is `human`,
`service` or `agent`, with an optional agent label. An **actor snapshot**
stores `initiating_client_id`, `actor_kind` and `agent_label` in the domain
record or append-only transition and its transactional event. It never resolves
historical attribution through a current credential or webhook subscriber.
`operator_actor` carries this request-local snapshot; it grants no permissions.

## Association and meetups

Association members belong to Verein openLEG Schweiz, independently of LEG
participants or product users. An application is not membership until admitted.
Association dues and receipts are not electricity billing or payment approval.
An occurrence is one actual meetup with a stable URL and calendar UID. A draft is
private; approval of public notes is explicit and independent of the event date.
See [association administration](docs/verein-administration.md).

Quarterly association reports are distinct from LEG electricity invoices, member
dues receipts and spending approvals. `verein_finance.py` reconciles CHF account
and fund balances without SQL. `store/verein_finance.py` owns private immutable
inputs, stable source identities and independently approved public versions.
Public reads select only redacted snapshots. No report executes a bank payment.

An **agent confirmation request** is a durable human gate before a VNB case
exists. It binds one community, originating credential, canonical payload,
fingerprint and adapter. Confirmation records `confirming_building_id` separately
from the immutable initiating actor. Expiry is a request event, not a VNB case.

`store.agent_confirmation` holds the pending-request lock and owns its transaction.
During confirmation, `store.vnb_exchange.confirmation_transaction` joins case,
package and event writes to that transaction through a context-local connection.
Other VNB submissions retain their existing transaction behaviour. This prevents
confirmation completion from committing separately from its business records.

An **OAuth grant** records one administrator's delegation to a pre-registered
MCP host for exactly one community and explicit operator capabilities.
Its downstream operator credential has actor kind `agent`. An **MCP access
token** identifies that grant for one MCP resource; it is not an operator token.
