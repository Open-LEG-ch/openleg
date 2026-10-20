# Community archive

The LEG dashboard exports `openleg-community-archive/2` JSON. The reader also
accepts the exact version 1 dataset contract. Older installations reject
version 2; upgrade the destination before importing a version 2 archive.
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

The archive still excludes statement reconciliation records, invoice questions,
role audit history and battery settings. These operating datasets remain follow-up work in #606.
This version does not establish complete operational portability.
