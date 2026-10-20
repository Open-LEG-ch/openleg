# Community archive

The LEG dashboard exports `openleg-community-archive/2` JSON. The reader also
accepts the exact version 1 dataset contract. Older installations reject
version 2; upgrade the destination before importing a version 2 archive.
Export requires a confirmed LEG administrator session. Dry-run and HTTP restore
require that session and CSRF protection. HTTP restore writes also require
platform administrator authentication; send the credential in `X-Admin-Token`.
Dry-run checks archive consistency, not VNB provenance or restore authority.
Trusted installation operators can use the low-level restore function.
Protect the file as private personal, metering and financial data.

The manifest declares the datasets and their SHA-256 hashes. The archive covers
those datasets; it does not include every private operations ledger. Binary
attachments, JSON fields and typed dates and quantities retain their values.
PostgreSQL column types determine scalar-tag decoding. Objects inside JSON or
JSONB fields remain literal JSON, even when a key resembles an archive type tag.
Restore rejects conflicting records and leaves unrelated records intact.

Run a dry-run against the destination before restoring. The destination must
recognize every archived column. Restore preserves imported identifiers and
advances their sequence counters so new records can follow. It preserves a
counter already ahead of the imported records. Counter changes share the restore
transaction. Writers that need the same sequences, including other communities,
can wait until the full restore commits. Reading rows are inserted in batches
to reduce database round trips; this does not remove the transaction-wide lock.

Dry-run reports differing records that already use an archived primary key;
identical records remain safe to repeat. Another unique constraint or a concurrent
write can still make the final restore refuse and roll back.

The supported destination uses ascending sequences with `CACHE 1`, as created
by OpenLEG's schema. Dry-run rejects a destination with incompatible sequence
settings. Restore rechecks the settings inside its transaction. The database
role must own these sequences, as it does when it creates the application schema.

Export captures a consistent point-in-time snapshot of the community. Later
source changes do not synchronize to the destination. Coordinate the final
export and cutover so source and destination do not run community mutations or
external ingestion concurrently.

An identical restore is safe to repeat. Export does not delete source records,
and restore does not change retention policy or establish legal retention periods.


Version 2 includes accepted VNB calculated-value deliveries, their original
source bytes, hashes, normalized quantities, dated mapping metadata and
append-only withdrawal events. It also retains the SDAT ledger identities of
accepted E66 sources after a correction replaces their physical readings.
Only accepted deliveries belonging to this community and its tenant are
included. Refused intake is excluded because its raw contents may belong to
unassigned communities. Import checks source hashes and the ownership of each
withdrawal before writing. A withdrawn delivery stays withdrawn after restore;
issued invoice snapshots remain unchanged.

Version 1 archives lack these source datasets. Import preserves their legacy
scope; it cannot reconstruct missing source evidence or make a source-less
billing draft approvable. Reimport the authorized original evidence when needed.

Version 2 also preserves VNB formation packages, membership mutation cases and
acknowledgement history, including original binary packages and response evidence.

Version 2 also retains statement imports and reconciliation decisions, private
invoice questions and their messages, attachments and status history, scoped
role audit, battery settings and cost shares. Community-owned operator events
keep their identifiers and payloads. Completed `case.respond:<query_id>` and
`payment.confirm:<entry_id>` responses migrate only when their parent question
or bank entry belongs to the archive. In-flight reservations, metering retry
keys tied to deployment job IDs and orphan replay keys are excluded. An API client
reenrolled on the destination can replay an earlier completed case/payment
request without appending a second transition.

Live operator credentials, webhook destinations/signing secrets, pending webhook
dispatch and credential rate-usage logs are excluded. Reenrol clients, configure
webhook subscriptions and assess any undelivered events explicitly on the
destination. Restoring an archive does not send old webhooks. Tenant-wide SDAT
scheduler settings and run logs are excluded because they can cover several
LEGs and contain installation paths. Configure destination credentials, tenant
mappings and external cron schedules separately; old runtime job identifiers do
not establish that a destination job ran.

Owner-only equipment, household telemetry, contact grants and platform operations
are outside the LEG administrator's export authority and remain excluded. The
archive covers the declared community operating datasets, not every deployment
record. #612 remains open for owner-approved operating facts and any
separately documented integration limits.
