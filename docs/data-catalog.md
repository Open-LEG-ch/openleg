# OpenLEG data catalog (#516)

One section per store domain. A maintainer reads this before touching data.
Each section states: what personal or energy data it holds, its purpose, its
owner inside a LEG, its sensitivity, whether output is resident-visible, and
whether a consent gate applies. Field names are cited from
`store/schema.py`; a test pins that the catalog keeps matching the schema.

## Data policy where it binds

Citizen meter data (smart-meter readings, 15-minute series, per-building
consumption) stays within each LEG. It is never sold and never aggregated for
third parties. Neighbour discovery output always carries the neighbour consent
gate: an inner join on `consents.share_with_neighbors`, fail closed, and a
count uses conditions identical to its list.

Operational household telemetry has its own explicit sharing grants; neighbour
consent and community administrator roles do not grant telemetry access.

## store/equipment

The equipment advisory workflow is documented under `store/advisories` below.

- Table: `equipment_records` (DDL in `store/equipment.py`).
- Holds: owner `building_id`, record UUID, creation/update times and a private
  JSON record. It contains manufacturer/model, firmware, logger, AC kW and
  optional PV DC kWp, cloud dependency, installer-access state, optional serial,
  named remote users, network details and private notes. Checklist, manual
  vendor-cloud review and backup capability each retain separate evidence kind,
  review date and optional public documentation URL.
- Purpose: owner inventory and preparation of installation-specific tasks for
  an installer. No scanning, telemetry ingestion or device-control commands.
- Owner: the currently verified building owner. Neither LEG membership nor
  telemetry enrollment is required. Every read, edit, download and deletion
  checks the owner session against the current verified building record.
- Sensitivity: private installation and access information. Neighbour consent,
  community administrator status and telemetry grants confer no access.
- Evidence: owner reports, device observations and owner-entered installer
  evidence stay distinct. Known sources need a non-future review date.
  OpenLEG does not verify these assertions. Manufacturer/model/firmware changes
  reset the checklist, cloud review and backup assertions to unknown.
- Handoff: an explicit owner action downloads allowlisted device facts,
  findings, action states, dates and public source links. It excludes profile
  data, serials, named remote users, network details and private notes. The
  owner reviews the remaining device fields and links before sharing. Nothing
  is emailed automatically. Responses use no-store and no-referrer headers.
- Retention: until equipment or profile deletion; the JSON evidence lives in
  the same row and the owner FK cascades on profile deletion. There is no
  independent checklist history. Downloads are outside database deletion.

## store/telemetry

- Tables: `telemetry_installations`, `telemetry_samples`,
  `telemetry_aggregates`, `telemetry_shares` (DDL in `store/telemetry.py`).
- Holds: installation/community/owner identity, hashed ingestion credential,
  device/source identity, sample sequence and deduplication fingerprint,
  metric, unit, value, direction, location, quality, cadence, source observation
  and server receipt times. Hourly descriptive statistics retain those dimensions.
- Derived state: `notice_refresh_pending` records deferred notice processing.
  It contains no sample or error details and clears on successful reconciliation.
- Purpose: private operational observations. No meter import, allocation,
  billing input, device credentials or control commands.
- Owner: the verified, confirmed community member who enrolled the installation.
- Sensitivity: household energy data; never sold or aggregated for third parties.
- Access: owner dashboard session, or an explicit `telemetry_shares` grant to a
  verified confirmed member of the same community. All reads, exports and
  aggregates check current access. Neighbour consent grants no access.
- Retention: product defaults of 7 days raw and 90 days aggregates; shorter
  whole-day settings and owner deletion supported. Read-time expiry and the
  cleanup cron enforce separate operational horizons. See `docs/telemetry.md`.
- Credentials: ingestion-only, installation/community-bound, hashed at rest;
  rotation preserves the sequence watermark. Deletion cascades all four tables.

## store/telemetry_notices

- Table: `telemetry_notices` (DDL in `store/telemetry_notices.py`, initialized
  with telemetry tables).
- Holds: installation reference, bounded condition/channel codes, episode count,
  active flag, raised/acknowledged/snoozed/resolved timestamps and recovery code.
  No copied samples, free text, device credentials or delivery addresses.
- Purpose: owner action prompts with one-hour acknowledgement cooldown and
  distinct recovery states. Derived on ingestion and owner reads.
- Sensitivity: private household operational state; never sold or aggregated
  for third parties. No access through sample sharing or administrator status.
- Access: current verified owner with confirmed community membership. Mutations
  require the dashboard session's CSRF token and current episode.
- Retention: active rows remain until resolution or installation deletion.
  Resolved rows use the installation's raw-data horizon, including read-time
  expiry and the existing cleanup cron. Installation, owner membership/profile
  and community deletion cascade the rows. No separate acknowledgement history.

## store/building

- **Tables:** `buildings`
- **Holds:** `building_id`, `email`, `address`, `lat`, `lon`, `plz`,
  `building_type`, `annual_consumption_kwh`, `potential_pv_kwp`, `user_type`,
  `verified`, `city_id`, `referrer_id`, `bfs_number`, `municipality_name`,
  `canton`, `roles`, `has_solar`, `preferred_locale` (German or Swiss French)
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
  `bfs_number`, `roles`, `has_solar`, `preferred_locale`, verification token and timestamps.
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
- Notification language follows the same canonical verified record and consent
  selection as the recipient. The email-only legacy reader retains its return shape.

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

## store/contact_request

- **Tables:** `contact_requests`, `contact_request_events` (schema in
  `store/contact_request.py`, called by `store/schema.py`).
- **Holds:** participant references, status, consent and expiry timestamps,
  profile fingerprints, explicitly approved email/phone snapshots, and events
  recording actor, action, field names and time. Events contain no contact values.
- **Purpose:** private contact requests between verified registered households
  in the same BFS municipality. Coverage-only interest records have no resident
  dashboard and are not candidates.
- **Owner:** the two participating residents, independently of LEG membership.
- **Sensitivity:** personal contact and consent data. These records are excluded
  from the community operations archive because a LEG administrator does not own
  either resident's private matching history.
- **Resident-visible:** each person sees only their requests. Before mutual
  acceptance, candidates have opaque, viewer-bound handles and no identifying
  details. After acceptance, only the other person's selected fields appear.
- **Consent gate:** both profiles must remain verified, have the same nonempty
  BFS number, different email identities, and allow neighbour discovery. Request
  and acceptance each require explicit field selection. Changed profile or
  discovery-consent fingerprints invalidate old grants. Private HTTP responses
  carry `no-store` and `no-referrer`. Notifications go separately to each person
  and contain no other participant's identity or contact details.

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

- **Tables:** `vnb_calculated_values_deliveries`, `vnb_calculated_values_events`
- **Holds:** VNB case, source, period, content and evidence fingerprints,
  normalized quarter-hour LEG allocations, validation diagnostics, and the
  original evidence bytes. Append-only withdrawal events preserve the source
  delivery ID, tenant/community scope, authenticated admin-token principal,
  operator's reason, request fingerprint, idempotency key and event time.
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

- **Tables:** `community_batteries`, `community_battery_shares`
- **Holds:** one shared storage battery (Quartierakku) per community:
  capacity in kWh, annual cost in CHF, its billing participant ID, and one
  cost share in percent per participant. The billing-period snapshot freezes
  charged, discharged, directly sourced and battery-sourced energy.
- **Purpose:** the shared asset behind the battery cost-share lines that the
  billing draft and the invoices carry.
- **Owner:** the LEG (operator records the asset and the shares).
- **Sensitivity:** financial data about the community, cost shares per
  building.
- **Resident-visible:** the asset and its shares appear on the LEG
  dashboard; members see the community's cost split.
- **Consent gate:** not neighbour-visible.

## store/invoice_query

- **Tables:** `invoice_queries`, `invoice_query_messages`,
  `invoice_query_events`
- **Holds:** an invoice-scoped question category and status, private messages,
  optional PDF evidence, and append-only status events with actor and time.
- **Purpose:** let a member question an immutable invoice and follow the
  operator's response without changing the invoice snapshot.
- **Owner:** the invoiced member and the issuing LEG.
- **Sensitivity:** financial correspondence and optional personal evidence.
- **Resident-visible:** only to the invoice participant; operators are scoped
  to the invoice's community.
- **Consent gate:** invoice ownership or community capability, not neighbour
  consent.

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

- **Tables:** `communities`, `community_members`, `community_role_events`
- **Holds:** the LEG record (`community_id`, `name`, `status`,
  `distribution_model`, dual-control policy), memberships (`role`,
  `access_roles`, `status`, `invited_by`), and append-only role changes.
- **Purpose:** LEG formation and membership lifecycle.
- **Owner:** the LEG (its members).
- **Sensitivity:** membership personal data; member aggregates shown to
  members are consent-gated (`fetch_community_with_members`,
  `fetch_user_communities`, pinned by #518).
- **Resident-visible:** to members of the community.
- **Consent gate:** applies to member-visible aggregates.

## store/formation_activation

- **Tables:** `communities`; reads source attachments from `correspondence_log`.
- **Holds:** `vnb_start_confirmation` with the incoming correspondence ID,
  SHA-256, VNB reference, confirmed start date, recording and activating actors,
  and UTC audit times; also sets `dso_approved_at` and `activated_at`.
- **Purpose:** two explicit transitions from submitted formation to confirmed
  start and active operation. A transport receipt never activates the LEG.
- **Owner:** the LEG.
- **Sensitivity:** community references, actor identifiers and linked evidence.
- **Resident-visible:** private community views; only a current confirmed
  administrator can record the evidence or activate. Other members' visibility
  still follows `share_with_neighbors`.
- **Retention:** the existing community record and correspondence attachment
  have no automated expiry. These operations add no cleanup or retention period.

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
  `share_with_utility`, `share_with_neighbors`, `preferred_locale`.
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

## store/operator_api

- **Tables:** `operator_api_clients`, `operator_api_usage`, `operator_events`,
  `operator_webhook_deliveries`
- **Holds:** community-scoped hashed API tokens, explicit capabilities,
  endpoint usage, lifecycle-event payloads and delivery outcomes.
- **Purpose:** private operator integration with bounded, observable webhook
  delivery.
- **Owner:** the LEG administration that created the credential.
- **Sensitivity:** credentials and private LEG workflow facts. API tokens are
  hashed and never returned after creation or rotation.
- **Resident-visible:** no; credential administration is restricted to a
  confirmed LEG administrator.
- **Consent gate:** none; tenant scope and explicit capabilities are mandatory.

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

## store/advisories

- Tables: `advisory_catalog`, `advisory_revisions`, `advisory_publications`,
  `advisory_review_policies`, `advisory_source_reviews`,
  `equipment_advisory_actions` (DDL in `store/advisories.py`).
- Catalog holds public-source URLs, publisher/revision/publication date,
  manufacturer/model names and explicit aliases, firmware bounds/profile,
  remedy text/links, withdrawals and correction notes. Content revisions and
  publication approvals are append-only. Approval binds an exact SHA-256 digest,
  named reviewer and review date; reviewer identifiers are private admin data.
- Source policy holds a private named review owner, bounded cadence, public
  scope description, exact covered source URLs and enabled state. New policy
  versions require their own complete successful manual source review. Review
  events retain failures and latest successful review dates. No feed or
  continuous collection is implemented.
- Owner evidence holds equipment/advisory/revision references, the exact saved
  manufacturer/model/firmware context and digest, acknowledgement, owner-declared
  remedy state, dated evidence and optional private note. It does not verify a
  remedy or change firmware matching.
- Access: catalog administration requires `X-Admin-Token`, including reads of
  reviewer identities. URL/form credentials are rejected. Equipment matches,
  acknowledgements and remedy notes require the current verified equipment
  owner's dashboard session. LEG administration, neighbour consent, telemetry
  grants and admin catalog credentials grant no equipment access.
- Output: owners receive published source facts, reasons, coverage/dates and
  source status. Drafts and private maintainer identifiers are omitted. Text is
  escaped; only validated public HTTPS documentation links are accepted. Every
  owner/admin response uses no-store and no-referrer headers.
- Purpose: private installation review with an installer. No external notices,
  firmware installation, settings changes or attack detection.
- Deletion: equipment/profile deletion cascades to all private owner action
  rows. Public source revisions, approvals and source-review history remain
  independent of registration deletion. Catalog/audit expiry is not implemented.
