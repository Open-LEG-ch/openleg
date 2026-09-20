# OpenLEG data catalog (#516)

One section per store domain. A maintainer reads this before touching data.
Each section states: what personal or energy data it holds, its purpose, its
owner inside a LEG, its sensitivity, whether output is resident-visible, and
whether a consent gate applies. Field names are cited from
`store/schema.py`; a test pins that the catalog keeps matching the schema.

## Data policy where it binds

Citizen meter data (smart-meter readings, 15-minute series, per-building
consumption) stays within each LEG. It is never sold and never aggregated for
third parties. Resident-visible output always carries the neighbour consent
gate: an inner join on `consents.share_with_neighbors`, fail closed, and a
count uses conditions identical to its list.

## store/building

- **Tables:** `buildings`
- **Holds:** `building_id`, `email`, `address`, `lat`, `lon`, `plz`,
  `building_type`, `annual_consumption_kwh`, `potential_pv_kwp`, `user_type`,
  `verified`, `city_id`, `referrer_id`, `bfs_number`, `municipality_name`,
  `canton`, `roles`, `has_solar`
- **Purpose:** resident registration and readiness; the map of consenting
  neighbours.
- **Owner:** the resident (each row is one household's record).
- **Sensitivity:** personal + energy; citizen meter data stays within each
  LEG and is never sold, never aggregated for third parties.
- **Resident-visible:** yes, but only consent-gated: `get_all_buildings`,
  `get_all_building_profiles` and `get_neighbor_count_near` inner-join
  consents on `share_with_neighbors = TRUE`. Self-views
  (`get_building`, `get_building_for_dashboard`) are `LEFT JOIN` by design
  because a member reads their own record.
- **Consent gate:** applies to every other-resident-visible output.

## store/interest

- **Tables:** `coverage_requests`; also reads verified rows from `buildings`.
- **Holds:** `email`, optional `address`, `plz`, `municipality_name`, `canton`,
  `bfs_number`, `roles`, `has_solar`, verification token and timestamps.
- **Purpose:** preserve demand when an address cannot be checked, publish an
  anonymised municipality count, notify verified participants when that count
  grows, and provide a private operator follow-up list.
- **Owner:** the person who submitted the interest.
- **Sensitivity:** personal. Public and Gemeinde outputs are aggregate only;
  names, addresses and contact details are never included.
- **Resident-visible:** only the municipality total, shown as `0`, `< 3`, or
  the exact count from three onward.
- **Consent gate:** email verification is required before any count or
  notification. Map visibility remains separately opt-in.

## store/consent

- **Tables:** `consents`, `data_consents`
- **Holds:** `share_with_neighbors`, `share_with_utility`, `updates_opt_in`,
  `consent_version`, `consent_timestamp`; `data_consents` carries the tier
  (`tier`), `share_with_municipality`, `share_anonymized_research`,
  `revoked_at`.
- **Purpose:** the live consent value every gate reads; revocation is
  immediate for future visibility (it does not delete past records).
- **Owner:** the resident.
- **Sensitivity:** consent data itself.
- **Resident-visible:** no direct output; it governs other outputs.
- **Consent gate:** this IS the gate.

## store/cluster

- **Tables:** `clusters`, `cluster_info`
- **Holds:** provisional cluster assignments (`building_id`, cluster id,
  scores).
- **Purpose:** formation suggestions before a LEG exists.
- **Owner:** the platform until a LEG forms; derived from consent-gated
  profiles only.
- **Sensitivity:** derived personal data; inherits the building domain's
  bounds.
- **Resident-visible:** only through consent-gated profile reads that feed
it.

## Community operations archive

- **Interface:** confirmed LEG administrators use the private
  `/leg/community/<community_id>/archive` controls to export, validate, and
  restore `openleg-community-archive/1` JSON.
- **Contents:** the selected community, its member profiles and consent
  metadata, formation and signed documents, correspondence and attachments,
  metering mappings and readings, billing policies and periods, line items,
  immutable invoices, delivery/correction records, and lifecycle events.
- **Scope:** every query starts from `community_id`; profiles are limited to
  current community members. Tokens, unrelated profiles, other communities,
  platform operations, and public reference data are excluded.
- **Manifest:** records SHA-256 hashes per dataset, kWh/kWp/CHF units,
  Europe/Zurich and UTC time-zone conventions, schema version, and source
  provenance. Binary values use tagged base64 objects; dates and decimals use
  tagged ISO/string values so a round trip does not lose their database type.
- **Restore:** dry-run checks version, hashes, record shape, relationships, and
  target conflicts without writes. Restore uses one database transaction,
  preserves identifiers and audit links, and ignores identical key conflicts
  on repeat runs.
- **Consent gate:** inherited from its inputs (`get_all_building_profiles`
  is gated).

## store/metering

- **Tables:** `metering_points`, `metering_point_readings`, `sdat_imports`,
  `sdat_veracity_flags`
- **Holds:** the VNB metering point registry (`metering_point_id`,
  `vnb_community_id`) and the 15-minute E66 series (`total_kwh`, `grid_kwh`,
  `community_kwh`, `direction`, `measured_at`); the import ledger and its
  veracity flags (`window_start`, `window_end`, `kind`).
- **Purpose:** billing-grade energy accounting per metering point.
- **Owner:** the LEG whose metering points are assigned to it; data arrives
  from the VNB (validated E66 deliveries).
- **Sensitivity:** citizen meter data. Stays within each LEG, never sold,
  never aggregated for third parties.
- **Resident-visible:** neighbour-consent-gated for neighbour views
  (pinned by #509's privacy contract and the consent audit #518); operators
  see their LEG's periods.
- **Consent gate:** applies to neighbour-visible shapes.

## store/calculated_values

- **Tables:** `vnb_calculated_values_deliveries`
- **Holds:** VNB case, source, period, content and evidence fingerprints,
  normalized quarter-hour LEG allocations, validation diagnostics, and the
  original evidence bytes.
- **Purpose:** independent VNB evidence for the billing allocation check.
- **Owner:** the LEG identified by `community_id`; access is tenant-scoped
  through its administrator's `city_id`.
- **Sensitivity:** citizen meter data and original VNB evidence. Raw evidence
  stays out of logs and operator list responses.
- **Resident-visible:** no; operators see only safe delivery metadata.
- **Consent gate:** not neighbour-visible; admin authentication and tenant
  scope apply.

## store/meter

- **Tables:** `meter_readings`
- **Holds:** manual/CSV smart-meter readings per building
  (`building_id`, reading date, `total_kwh`).
- **Purpose:** consumption input for readiness and early billing runs
  before SDAT metering exists.
- **Owner:** the resident.
- **Sensitivity:** citizen meter data; same LEG bounds.
- **Resident-visible:** own data only.
- **Consent gate:** not neighbour-visible.

## store/sdat_ingestion

- **Tables:** `sdat_ingestion_schedules`, `sdat_ingestion_runs`
- **Holds:** tenant schedule settings, timestamps, safe status/error codes, and
  aggregate imported file and reading counts. It does not store file names,
  credentials, document contents, or metering point IDs.
- **Purpose:** scheduled SDAT operation, overlap prevention, and recovery audit.
- **Owner:** the LEG operator for the configured tenant.
- **Sensitivity:** operational metadata. Aggregate reading counts can disclose
  delivery volume and remain operator-only.
- **Resident-visible:** no.
- **Consent gate:** not applicable; admin and cron authentication apply.

## store/billing

- **Tables:** `billing_periods`, `billing_tariffs`, `invoices`,
  `billing_line_items`, `invoice_corrections`, `invoice_lifecycle_events`,
  `invoice_delivery_jobs`
- **Holds:** tariffs (`internal_price_chf_per_kwh`, `grid_fee_chf_per_kwh`,
  frozen `policy_snapshot`), per-member invoice lines, lifecycle and delivery
  state.
- **Purpose:** the LEG's financial accounting and member invoices.
- **Owner:** the LEG (operator approves); members see their own invoices.
- **Sensitivity:** financial personal data.
- **Resident-visible:** own invoices only; the operator workspace is
  admin-gated. Member display fails closed on unreadable figures (#528).
- **Consent gate:** not neighbour-visible.

## store/battery

- **Tables:** `billing_storage_assets`, `billing_storage_shares`
- **Holds:** one shared storage asset (Quartierakku) per community: `name`,
  `capacity_kwh`, `annual_cost_chf`, and per-participant `share_pct`.
- **Purpose:** the cost share of the shared battery that every billed
  participant carries.
- **Owner:** the LEG (billing preparation writes); members see their own
  share.
- **Sensitivity:** financial configuration data.
- **Resident-visible:** the operator dashboard section is capability-gated.
- **Consent gate:** not neighbour-visible.

## store/profile

- **Tables:** `municipalities`, `municipality_profiles`,
  `municipality_pv_panel`, `elcom_tariffs`, `sonnendach_municipal`
- **Holds:** public energy facts per Gemeinde (tariffs in `total_rp_kwh`,
  `grid_rp_kwh`, solar potential, production/consumption MWh).
- **Purpose:** public Gemeindeprofil pages and the value-gap calculation.
- **Owner:** public data (ElCom, BFE Sonnendach, Energie Reporter), refreshed
  by the platform.
- **Sensitivity:** public; figures name their basis (`data_sources`,
  provenance panel).
- **Resident-visible:** yes, public by design.
- **Consent gate:** none (no personal data). Reads are cached per
  municipality (#527) with refresh-seam invalidation.

## store/email_queue

- **Tables:** `scheduled_emails`
- **Holds:** `building_id`, `email` (recipient address), `template_key`,
  `send_at`, `status`, `error_message`, `retry_count`.
- **Purpose:** outbound system emails (welcome, magic links, invoices,
  nudges).
- **Owner:** the platform, on behalf of the LEG.
- **Sensitivity:** personal (addresses).
- **Resident-visible:** no. Hygiene pinned by #519: addresses scrubbed on
  send/cancel, bounded retries, 90-day terminal cleanup, FK `ON DELETE
  CASCADE`.

## store/utility

- **Tables:** `utility_clients`
- **Holds:** VNB/EVU contacts (`company_name`, `contact_email`,
  `kanton`, service territory).
- **Purpose:** routing LEG applications and consents to the right grid
  operator.
- **Owner:** the utility (business contact data).
- **Sensitivity:** business contact data.
- **Resident-visible:** no.
- **Consent gate:** none.

## store/referral

- **Tables:** `referrals`, `street_stats`
- **Holds:** invite edges (`referrer_id`, `referred_building_id`),
  aggregated street counts.
- **Purpose:** organic growth; the street leaderboard.
- **Owner:** the LEG community (aggregate) / the referrer (edge).
- **Sensitivity:** derived personal data.
- **Resident-visible:** the leaderboard is resident-visible and
  consent-gated (inner join on `share_with_neighbors`, pinned by #518).
- **Consent gate:** applies. `street_stats` currently has no readers or
  writers - deletion candidate (see `docs/consent-gate-audit.md`).

## store/document

- **Tables:** `leg_documents`, `community_documents`
- **Holds:** generated formation documents and signing status
  (`deepsign` references).
- **Purpose:** the LEG's founding and membership paperwork.
- **Owner:** the LEG.
- **Sensitivity:** legal/personal (member names in documents).
- **Resident-visible:** own community's documents.
- **Consent gate:** membership-gated, not neighbour-gated.

## store/formation

- **Tables:** `communities`, `community_members`
- **Holds:** the LEG record (`community_id`, `name`, `status`,
  `distribution_model`) and memberships (`role`, `status`, `invited_by`).
- **Purpose:** LEG formation and membership lifecycle.
- **Owner:** the LEG (its members).
- **Sensitivity:** membership personal data; member aggregates shown to
  members are consent-gated (`fetch_community_with_members`,
  `fetch_user_communities`, pinned by #518).
- **Resident-visible:** to members of the community.
- **Consent gate:** applies to member-visible aggregates.

## store/formation_documents

- **Tables:** none of its own (writes `community_documents` atomically).
- **Holds:** one LEG's generated document bundle (see store/document).
- **Purpose:** atomic multi-document persistence during formation.
- **Owner:** the LEG.
- **Sensitivity:** legal/personal.
- **Resident-visible:** own community.
- **Consent gate:** membership-gated.

## store/correspondence

- **Tables:** `correspondence_log`
- **Holds:** the shared journal (`direction`, `channel`, `counterparty`,
  `subject`, `notes`, `attachment_filename`) per community.
- **Purpose:** the LEG's common in/out record with the VNB and authorities.
- **Owner:** the LEG collectively.
- **Sensitivity:** business correspondence with personal traces.
- **Resident-visible:** to members of the community.
- **Consent gate:** membership-gated.

## store/vnb_exchange

- **Tables:** `vnb_submission_cases`, `vnb_mutation_cases`,
  `vnb_mutation_events`
- **Holds:** versioned contract and capability snapshots, immutable payload
  fingerprints, lifecycle state, actors, timestamps, private handover packages,
  VNB responses and append-only mutation evidence.
- **Purpose:** idempotent, auditable formation and membership exchange without
  coupling the LEG record to one transport.
- **Owner:** the LEG.
- **Sensitivity:** legal, personal and operational. Ordinary case reads exclude
  private package and response bytes.
- **Resident-visible:** only to authorized community members.
- **Consent gate:** membership and explicit workflow capability.

## store/dashboard_profile

- **Tables:** none of its own; it is the seam for the resident's
  self-service writes to `buildings` and `consents`.
- **Holds:** updates `annual_consumption_kwh`, `potential_pv_kwp`,
  `share_with_utility`, `share_with_neighbors`.
- **Purpose:** the resident's own profile and consent changes.
- **Owner:** the resident.
- **Sensitivity:** personal + energy.
- **Resident-visible:** own data only.
- **Consent gate:** writes the values the gate reads.

## store/token

- **Tables:** `tokens`
- **Holds:** single-use tokens (`token_type`: verification, unsubscribe,
  magic link) hashed, with expiry.
- **Purpose:** one-time links for verification, unsubscribe and login.
- **Owner:** the platform for the resident.
- **Sensitivity:** auth data (hashed at rest).
- **Resident-visible:** no.
- **Consent gate:** none.

## store/access_token

- **Tables:** `dashboard_access_tokens`, `municipality_access_tokens`
- **Holds:** hashed, single-use dashboard and municipality access tokens
  (`token_hash`, `expires_at`, `used_at`).
- **Purpose:** passwordless session establishment.
- **Owner:** the platform for the resident/Gemeinde.
- **Sensitivity:** auth data (hashed at rest).
- **Resident-visible:** no.
- **Consent gate:** none.

## store/analytics

- **Tables:** `analytics_events`
- **Holds:** event log (`event_type`, `building_id`, `data` JSONB) and
  aggregate counts.
- **Purpose:** product analytics for the platform operator.
- **Owner:** the platform.
- **Sensitivity:** pseudonymous usage data tied to `building_id`; no meter
  data, no billing data.
- **Resident-visible:** no.
- **Consent gate:** none (never feeds a resident-visible surface).

## store/tenant

- **Tables:** `white_label_configs`, `webhooks`
- **Holds:** per-territory white-label config (names, colours,
  `solar_kwh_per_kwp` override, PLZ ranges) and webhook endpoints.
- **Purpose:** multi-territory deployment configuration.
- **Owner:** the platform / the territory operator.
- **Sensitivity:** configuration, not personal.
- **Resident-visible:** branding only.
- **Consent gate:** none.

## store/api_client

- **Tables:** `api_clients`, `api_usage`
- **Holds:** public API clients (`client_id`, hashed `client_secret`) and
  per-call usage counters.
- **Purpose:** authenticated public API access and metering of it.
- **Owner:** the platform / the API consumer.
- **Sensitivity:** credentials (hashed at rest), usage volumes.
- **Resident-visible:** no.
- **Consent gate:** none.

## store/registry

- **Tables:** `leg_registry`
- **Holds:** registered LEGs (`community_name`, `kanton`, `plz`, member
  count, `verified` status).
- **Purpose:** the public LEG directory and federation verification.
- **Owner:** the LEG (it registers itself).
- **Sensitivity:** business facts a LEG chose to publish.
- **Resident-visible:** yes, public by design.
- **Consent gate:** none.

## store/ops

- **Tables:** `lea_reports`, `ops_snapshots`
- **Holds:** autonomous job reports (`job_name`, `summary_text`, `status`)
  and operational snapshots (`source`, `category`, `status`,
  `summary_text`, `payload`).
- **Purpose:** operator diagnostics.
- **Owner:** the platform operator.
- **Sensitivity:** operational; payloads must never contain metering-point
  identifiers (pinned by import-script masking tests).
- **Resident-visible:** no; admin-gated.
- **Consent gate:** none.

## store/ranking

- **Tables:** none (reads the ranking snapshot files).
- **Holds:** league placements derived from public profile data.
- **Purpose:** the municipality ranking panel.
- **Owner:** platform, from public data.
- **Sensitivity:** public.
- **Resident-visible:** yes, public by design.
- **Consent gate:** none.

## store/municipality

- **Tables:** none of its own (serves the Gemeinde onboarding flows over
  `municipality_profiles` and `municipality_access_tokens`).
- **Holds:** Gemeinde onboarding state and its access tokens.
- **Purpose:** Gemeinde self-service onboarding.
- **Owner:** the Gemeinde.
- **Sensitivity:** business contact data.
- **Resident-visible:** no.
- **Consent gate:** none.

## store/invoice_query

- **Tables:** `invoice_queries`, `invoice_query_messages`, `invoice_query_events`.
- **Holds:** invoice-scoped questions, replies, and append-only status history.
- **Sensitivity:** private billing correspondence; operator API projections omit messages and attachments.
- **Resident-visible:** only to the invoice owner; operator access is community-scoped.

## store/operator_api

- **Tables:** `operator_api_clients`, `operator_api_usage`, `operator_events`, `operator_webhook_deliveries`.
- **Holds:** hashed credentials, capabilities, minimal signed events, and delivery state.
- **Sensitivity:** private operational integration data; tokens are never stored recoverably.
- **Resident-visible:** no.

## store/operator_operations

- **Tables:** reads operational domain tables and writes `operator_action_idempotency`.
- **Holds:** stable idempotency responses for scoped case and payment actions.
- **Sensitivity:** private operational data; read models redact invoice snapshots, correspondence, and source evidence.
- **Resident-visible:** no.
