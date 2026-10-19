# VNB exchange contract

OpenLEG keeps LEG-controlled records separate from the duties of the distribution system operator (VNB). The first contract version, `vnb-formation/1`, covers a signed formation package.

## Interface

An adapter declares its key, contract version and capabilities. Automated formation submission is available only when the adapter explicitly declares `formation_submission`. OpenLEG does not infer support from another operation or from the presence of a method.

The formation package contains a canonical manifest and the stored signed documents. Its SHA-256 fingerprint binds the community, adapter, contract version, ordered document identifiers, types, filenames and content hashes. Repeating the same package returns the existing case.

An adapter returns one of `delivered`, `acknowledged`, `rejected` or `failed`. It cannot read or change OpenLEG's database. OpenLEG alone checks authorization, document state and the guarded formation transition.

## Manual handover

`manual-handover` is the fallback when no automated transport is configured. It declares no automated capability. OpenLEG creates a private ZIP containing `manifest.json` and the signed documents, then records the case as `prepared`.

An authorized document operator downloads the package, sends it through the VNB's accepted channel and confirms delivery. Confirmation and the transition from `signatures_pending` to `dso_submitted` occur in one database transaction. Merely preparing or downloading a package never marks it as sent.

## Confirmed operating start

A transport acknowledgement confirms receipt, not the VNB's permission to start.
After submission, a confirmed community administrator records the actual start
confirmation as incoming correspondence with an attachment. The dashboard then
asks for that entry, the VNB's reference and its confirmed start date, plus an
explicit statement that the administrator has checked the evidence.

`POST /leg/community/{community_id}/vnb-start-confirmation` binds the original
attachment's SHA-256, correspondence ID, reference, start date, recording actor
and time to the community under `vnb-start/1`. It sets `dso_approved`, without
activating billing. Identical retries preserve the original record; changed
confirmations require a separate review and cannot overwrite it here.

`POST /leg/community/{community_id}/activate` requires a current confirmed
administrator and intact evidence. It permits explicit activation on or after
the confirmed date in Europe/Zurich, and records the activating actor and time.
Both operations require a dashboard session and CSRF protection. Neither route
contacts a VNB, decides eligibility or verifies the contents of its statement.
An active LEG still needs the existing billing prerequisites before invoicing.

## Adding an adapter

1. Implement the `VnbAdapter` interface in `vnb_exchange.py`.
2. Return exactly `vnb-formation/1` and an explicit capability set.
3. Translate the VNB response into a `DeliveryResult`; do not update formation state or persistence from the adapter.
4. Run the common exchange contract tests for supported, unsupported, replay, rejection and failure outcomes.
5. Keep document bytes, credentials and external identifiers out of logs.

No adapter may claim support for a VNB without a documented transport contract and representative fixtures.

## Participant mutations

The shared exchange also defines `vnb-membership/1`. Adapters declare each
supported operation separately: `participant_join`, `participant_exit`, and
`participant_change`. A missing capability always produces the same tracked
manual handover lifecycle; it never silently skips delivery.

Every mutation carries an operator-supplied stable mutation ID, effective date,
participant ID, source-agreement ID, and immutable before/after facts. OpenLEG
derives the before facts from the community record. VNB acknowledgements,
rejections, failures, and supersession are appended to the evidence ledger and
only update the VNB processing projection. They do not change the LEG-controlled
membership or agreement.

Mutation IDs are idempotency keys within a community. Reusing an ID for changed
facts is rejected. A second pending mutation for the same participant is also
rejected with an instruction to finish or supersede the open mutation. All reads,
downloads, and writes require membership-management authority in the same
community.
