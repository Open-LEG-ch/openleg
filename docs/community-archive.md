# Community archive

The LEG dashboard exports and restores `openleg-community-archive/1` JSON.
Export requires a confirmed administrator session.
Dry-run and restore require the same authority and CSRF protection. Protect the
file as private personal, metering and financial data.

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

The supported destination uses ascending sequences with `CACHE 1`, as created
by OpenLEG's schema. Dry-run rejects a destination with incompatible sequence
settings. Restore rechecks the settings inside its transaction. The database
role must own these sequences, as it does when it creates the application schema.

An identical restore is safe to repeat. Export does not delete source records,
and restore does not change retention policy or establish legal retention periods.
