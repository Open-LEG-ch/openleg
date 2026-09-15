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

The community operations archive is a portability copy, not a deletion or
retention trigger. It carries the source domains' retention meaning with it.
An administrator must protect exported files as personal, metering, and
financial data. Import dry-run never stores the file. A successful restore
recreates the source identifiers and relationships under the retention rules
below; it does not extend or shorten them.

Invoice-question response and reminder dates use
`INVOICE_QUERY_RESPONSE_DAYS` (default 10) and
`INVOICE_QUERY_REMINDER_DAYS` (default 2 days before the response deadline).

| Domain (catalog) | Retention horizon | Deletion trigger | What deletion reaches | What it deliberately keeps |
|---|---|---|---|---|
| store/building | Life of the registration | Profile deletion (unsubscribe confirmation) | The `buildings` row; CASCADE removes consents, tokens, access tokens, queue rows, cluster assignments, memberships, meter CSV readings; `referrer_id` edges become `SET NULL` | Nothing - the registration is the data |
| store/consent | Life of the registration | Profile deletion (CASCADE) | Both `consents` and `data_consents` rows | Revocation alone keeps the rows (visibility change, not deletion) |
| store/cluster | Until the cluster resolves or the profile is deleted | Profile deletion (CASCADE on `clusters`) | Provisional assignments | Formation outcomes (`communities`) survive |
| store/metering | Life of the LEG's accounting | Not implemented; metering points detach (`ON DELETE SET NULL`) on profile deletion | The link to the deleted building; readings and ledger stay | Readings and the SDAT ledger: the VNB's validated data is the billing basis; audit trail |
| store/calculated_values | Life of the LEG's accounting | Not implemented | - | Original VNB evidence, normalized allocations, validation outcome, and replay fingerprint |
| store/operator_api | Credential lifetime plus operational audit period | Credential revocation; audit cleanup not implemented | Active API access | Hashed credential and delivery audit records |
| store/operator_operations | Operational replay window | Expiry cleanup not implemented | - | Minimal idempotency responses |
| store/sdat_ingestion | Life of the tenant operation; no automated horizon yet | Not implemented | - | Schedule settings and run metadata with safe error codes and aggregate counts; no filenames, credentials, document contents, or metering point IDs |
| store/meter | Life of the registration | Profile deletion (CASCADE on `meter_readings`) | CSV readings | Nothing |
| store/billing | 10 years (Swiss OR accounting retention; policy, not code) | Not implemented | - | Invoices, line items, corrections, lifecycle events, delivery jobs: deliberately kept; `invoices.participant_id` has no FK, so profile deletion does NOT reach them |
| store/profile | Life of the deployment | Not implemented | - | Public energy facts are public data |
| store/email_queue | 90 days past terminal state | `cleanup_finished_emails` (#519) | Terminal rows (`sent`, `failed`, `cancelled`) past `EMAIL_QUEUE_RETENTION_DAYS = 90` | Pending rows in their retry window; addresses already scrubbed on send/cancel |
| store/utility | Life of the utility account | Not implemented | - | Business contacts |
| store/referral | Life of the registration | Profile deletion (`referred_id` CASCADE, `referrer_id` SET NULL) | Edges where the deleted building was referred; the referrer edge survives anonymised (`SET NULL`) | Aggregate street counts; `street_stats` is an unused table (deletion candidate) |
| store/document | Life of the LEG | Not implemented | - | Signed and signing documents |
| store/formation | Life of the LEG | Not implemented | Profile deletion CASCADEs `community_members`; the `communities` row survives with a dangling `admin_building_id` (no FK action) | The LEG record and its accounting |
| store/formation_documents | Life of the LEG | Not implemented | - | The document bundle |
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
| store/municipality | Life of the Gemeinde account | Not implemented | - | Onboarding state |

## Overstated promises (filed, not silently shipped)

1. `templates/unsubscribe.html` success copy says "Ihre Daten wurden
   erfolgreich geloescht." The code's reach is the registration profile and
   its cascades; issued invoices (billing accounting), analytics event rows
   and the detached metering points are deliberately or structurally kept.
   Filed: wording decision for a human.
2. `templates/datenschutz.html` section 6 claims unconfirmed "Matches" are
   deleted "nach angemessener Frist" - there is no matching concept and no
   deadline deletion in code. Filed.
3. "Server-Logs loeschen wir regelmaessig" (datenschutz.html) - hosting-level
   reach, outside this repository's code.

The wording decisions themselves are human calls; this matrix records what
the code actually reaches so the claims can be brought in line.
