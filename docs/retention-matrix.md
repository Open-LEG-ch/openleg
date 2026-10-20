# Retention and deletion matrix (#530)

One row per domain from `docs/data-catalog.md` (same domain names). This is
the statement of how long data lives, what unsubscribe and deletion actually
reach, and what deletion deliberately keeps.

**Revocation is not deletion.** Consent revocation (`share_with_neighbors` -> FALSE, `data_consents.revoked_at`) changes future visibility only: the
neighbour gate stops showing the building, and past records stay. No
user-facing promise may claim that revoking consent deletes records.

**Deletion triggers in code:** profile deletion via the unsubscribe
confirmation (`store/token.py::confirm_profile_deletion`, one transaction,
`DELETE FROM buildings` with its FK cascades) and queue/terminal cleanup
(#519). LEG dissolution and retention horizons beyond the queue are not
implemented in code; where the horizon is a legal or policy claim, it says so
below.

Current community and municipality administrator references block profile
deletion through `NO ACTION` foreign keys while they still point to that
profile. The [privacy review packet](privacy-notice-review.md)
records the owner decisions still needed.

The community operations archive is a portability copy, not a deletion or
retention trigger. It carries the source domains' retention meaning with it.
An administrator must protect exported files as personal, metering, and
financial data. Import dry-run never stores the file. A successful restore
recreates the source identifiers and relationships under the retention rules
below; it does not extend or shorten them.

Invoice-question response and reminder dates use
`INVOICE_QUERY_RESPONSE_DAYS` (default 10) and
`INVOICE_QUERY_REMINDER_DAYS` (default 2 days before the response deadline).

The German/French recipient preference in building and coverage records follows
the same retention and deletion rules as its registration. It has no separate
history or retention period. Scheduled email delivery reads the current choice.

| Domain (catalog) | Retention horizon | Deletion trigger | What deletion reaches | What it deliberately keeps |
|---|---|---|---|---|
| store/building | Life of the registration | Profile deletion (unsubscribe confirmation) | The `buildings` row; CASCADE removes consents, tokens, access tokens, queue rows, cluster assignments, memberships, meter CSV readings; `referrer_id` edges become `SET NULL` | Nothing - the registration is the data |
| store/interest | Unverified: `UNVERIFIED_INTEREST_RETENTION_DAYS = 30` from the latest registration (legacy buildings fall back to original creation); verified unresolved request: `VERIFIED_COVERAGE_RETENTION_MONTHS = 12` | Scheduled `cleanup_expired_interest`; email-confirmed unsubscribe | Expired `coverage_requests` and unverified `buildings`; unsubscribe deletes its bound building or coverage record and its tokens | Other registrations, including later submissions with the same email, need their own deletion confirmation |
| store/consent | Life of the registration | Profile deletion (CASCADE) | Both `consents` and `data_consents` rows | Revocation alone keeps the rows (visibility change, not deletion) |
| store/contact_request | Pending requests expire after 30 days; accepted grants last until revoked or invalidated. Terminal history remains until either profile is deleted. | Either participant revokes; a read or transition detects expiry or changed eligibility; either profile is deleted | Terminal transitions erase both contact snapshots. Profile deletion cascades requests and their events. | Consent timestamps, actor references, selected field names and profile fingerprints remain after revocation, but not after profile deletion. Expiry is enforced at access time; snapshot cleanup is lazy, with no scheduled cleanup yet. |
| store/cluster | Until the cluster resolves or the profile is deleted | Profile deletion (CASCADE on `clusters`) | Provisional assignments | Formation outcomes (`communities`) survive |
| store/metering | Life of the LEG's accounting | Not implemented; metering points detach (`ON DELETE SET NULL`) on profile deletion | The link to the deleted building; readings and ledger stay | Readings, the SDAT ledger and dated operator assignment statements; binding changes clear current validity |
| store/telemetry | Product defaults: raw 7 days, hourly statistics 90 days; owner can shorten to 1–7 and 1–90 days respectively. These are not legal or billing retention requirements. | Read-time expiry; `POST /api/cron/cleanup-telemetry` physically deletes expired rows; shorter settings clean immediately; owner deletes an installation; owner membership/profile or community deletion cascades | Installation deletion removes samples, statistics, grants and hashed credential. Raw expiry considers both observation and receipt time; missing observations use receipt only for deletion. Statistics expire by observation hour. | A sequence watermark survives expiry and credential rotation to reject replay. Credential revocation preserves history until its horizon; sharing revocation removes viewer access immediately but does not delete owner history. Installation deletion keeps nothing. |
| store/telemetry_notices | Active condition rows last until resolution or installation deletion; resolved rows expire after the installation's raw-data retention period. | Read-time expiry; telemetry cleanup physically deletes expired resolved rows; installation, membership/profile or community deletion cascades | Condition, episode, acknowledgement, cooldown and recovery state | No independent history. An active row is bounded by installation, condition and channel. |
| store/equipment | Until the owner deletes the equipment or registration | Owner equipment deletion; profile deletion cascades through `owner_building_id` | The equipment row including serial, network details, private notes, named remote users, checklist, manual cloud review and backup evidence | No database history survives. Owner-downloaded handoffs remain outside application deletion. |
| store/advisories | Public source and maintainer audit: deployment lifetime, no expiry implemented. Private equipment actions: until equipment/profile deletion. | Equipment/profile deletion cascades owner action rows | Device-context snapshots, acknowledgement, remedy evidence and private notes | Public source revisions, approvals, policy/review history and private maintainer identifiers remain independently of household profiles. |
| store/calculated_values | Life of the LEG's accounting | Not implemented | Withdrawal changes billing eligibility, without deleting records | Original VNB evidence, exact E66 channels, source precision/quality profile, frozen dated mappings and correction lineage, normalized allocations, validation outcome, replay fingerprint and append-only withdrawal events |
| store/operator_api | Credential lifetime plus operational audit period | Credential revocation; audit cleanup not implemented | Active API access | Hashed credential and delivery audit records |
| store/operator_operations | Operational replay window | Expiry cleanup not implemented | - | Minimal idempotency responses |
| store/sdat_ingestion | Life of the tenant operation; no automated horizon yet | Not implemented | - | Schedule settings and run metadata with safe error codes and aggregate counts; no filenames, credentials, document contents, or metering point IDs |
| store/meter | Life of the registration | Profile deletion (CASCADE on `meter_readings`) | CSV readings | Nothing |
| store/billing | 10 years (Swiss OR accounting retention; policy, not code) | Not implemented | - | Invoices, line items, corrections, lifecycle events, delivery jobs: deliberately kept; `invoices.participant_id` has no FK, so profile deletion does NOT reach them |
| store/battery | Life of the LEG's accounting | Community deletion cascades; no separate deletion flow | Battery configuration and participant cost shares | Billing-period snapshots remain with the accounting record |
| store/invoice_query | Same horizon as the questioned invoice; policy, not code | Not implemented | - | Questions, messages, PDF evidence and status history remain with the accounting record |
| store/profile | Life of the deployment | Not implemented | - | Public energy facts are public data |
| store/email_queue | 90 days past terminal state | `cleanup_finished_emails` (#519) | Terminal rows (`sent`, `failed`, `cancelled`) past `EMAIL_QUEUE_RETENTION_DAYS = 90` | Pending rows in their retry window; addresses already scrubbed on send/cancel |
| store/utility | Life of the utility account | Not implemented | - | Business contacts |
| store/referral | Life of the registration | Profile deletion (`referred_id` CASCADE, `referrer_id` SET NULL) | Edges where the deleted building was referred; the referrer edge survives anonymised (`SET NULL`) | Aggregate street counts; `street_stats` is an unused table (deletion candidate) |
| store/document | Life of the LEG | Not implemented | - | Signed and signing documents |
| store/formation | Life of the LEG | Not implemented | Ordinary member profile deletion CASCADEs `community_members`. A current administrator's profile deletion is blocked by `communities.admin_building_id` (`NO ACTION`) until the retaining reference changes. | The LEG record and its accounting |
| store/formation_documents | Life of the LEG | Not implemented | - | The document bundle |
| store/formation_activation | Follows the community and correspondence records | Not implemented | No deletion through activation or consent revocation | Start-confirmation projection, source reference and actor/time audit fields; original evidence stays in `correspondence_log` |
| store/correspondence | Life of the LEG | Not implemented | - | The shared journal is the LEG's memory |
| store/vnb_exchange | Life of the LEG | Community deletion cascades; no separate deletion flow | Formation and membership cases, private packages and response evidence | Nothing after community deletion; ordinary reads exclude private blobs |
| store/dashboard_profile | Follows its tables | Profile deletion | Rows via CASCADE (see store/building) | Nothing of its own |
| store/token | Until used or expired | Profile deletion (CASCADE); expiry at use time | The token rows | Nothing |
| store/access_token | Until used or expired | Profile deletion (CASCADE on `dashboard_access_tokens`) | The token rows | Nothing; hashes make leftovers useless |
| store/analytics | Unlimited today (finding, filed) | Not implemented | `building_id` has no FK: profile deletion leaves orphaned event rows | - |
| store/tenant | Life of the deployment | Not implemented | - | Configuration |
| store/api_client | Life of the deployment | Not implemented | - | Credentials are hashed |
| store/registry | Until the LEG withdraws | Withdrawal flow, not profile deletion | The registry entry | - |
| store/ops | Unlimited today (finding, filed) | Not implemented | - | Job reports and snapshots; payloads are masked |
| store/ranking | Follows the snapshot files | Snapshot regeneration | Replaced snapshots | - |
| store/municipality | Life of the Gemeinde account | Not implemented | A current administrator's profile deletion is blocked by `municipalities.admin_building_id` (`NO ACTION`) until the retaining reference changes. | Onboarding state |

## Contact request audit and limits (#567)

Request, acceptance, decline, revocation and detected expiry append events in
the same transaction as the state change. Acceptance records the recipient's
field selection; the original request records the sender's selection. An
expired request cannot be accepted. Either participant can withdraw a pending
request or revoke an accepted grant. Changes to verification, municipality,
contact values or the discovery-consent timestamp invalidate the grant on
the next private read or transition.

One request per unordered household pair prevents repeated requests after a
decline, expiry or revocation. This release has no reopening flow. At most
50 eligible candidates appear per view. Household labels are temporary list
positions, not names or addresses. Municipality matching does not establish
regulated LEG eligibility.

Email delivery is best effort after commit. Failure leaves the request intact
and is reported to the actor; both participants can read its state in their
dashboard. Emails contain no contact values and delivery errors do not log
recipient addresses. No email retry queue is added for this feature.

Revocation stops future disclosure and erases stored contact snapshots. It
cannot remove contact data someone already read or copied. Deleting either
profile deletes the pair's request and events through foreign-key cascades;
no separate matching history survives in this database. Backups and outbound
email-provider retention remain governed by the deployment's policies.

## Overstated promises and review status

1. `templates/unsubscribe.html` and the unsubscribe route/email in `app.py`
   scope deletion to the registration bound to the confirmation link: an address
   profile or an unresolved coverage request. Before confirmation, the page
   states that issued invoices, billing-relevant metering data and usage events
   remain. The deletion code and retention horizons are unchanged. The complete
   privacy notice still requires the named legal or DPO review in #558.
2. Hosting-level log retention remains outside this repository's code.
   The former unsupported promise in `templates/datenschutz.html` was removed;
   the hosting policy itself remains filed for operational definition.

This matrix records code behaviour. It does not record legal approval of the
privacy notice or ratify a hosting retention policy.

## Reviewed advisories (#619)

Catalog revisions, publication approvals, policy revisions and manual source
reviews remain for the deployment lifetime; no automated cleanup is implemented.
These rows retain public evidence/corrections/withdrawals and private maintainer
identifiers. They do not reference a household registration.

`equipment_advisory_actions` lasts until the owner deletes the equipment or
registration. Both routes cascade to its device-context snapshots,
acknowledgements, remedy evidence and private notes. Corrections and firmware
changes preserve earlier owner evidence for the same equipment but do not reuse
its acknowledgement for the new source revision/device context. No evidence is
sent to an external recipient. Database deletion does not alter any copies an
owner made outside the application.
