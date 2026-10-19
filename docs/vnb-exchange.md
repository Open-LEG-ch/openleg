# VNB exchange contract

OpenLEG keeps LEG-controlled records separate from the duties of the distribution system operator (VNB). The first contract version, `vnb-formation/1`, covers a signed formation package.

## Interface

An adapter declares its key, contract version and capabilities. Automated formation submission is available only when the adapter explicitly declares `formation_submission`. OpenLEG does not infer support from another operation or from the presence of a method.

The formation package contains a canonical manifest and the stored signed documents. Its SHA-256 fingerprint binds the community, adapter, contract version, ordered document identifiers, types, filenames and content hashes. Repeating the same package returns the existing case.

An adapter returns one of `delivered`, `acknowledged`, `rejected` or `failed`. It cannot read or change OpenLEG's database. OpenLEG alone checks authorization, document state and the guarded formation transition.

## Manual handover

`manual-handover` is the fallback when no automated transport is configured. It declares no automated capability. OpenLEG creates a private ZIP containing `manifest.json` and the signed documents, then records the case as `prepared`.

An authorized document operator downloads the package, sends it through the VNB's accepted channel and confirms delivery. Confirmation and the transition from `signatures_pending` to `dso_submitted` occur in one database transaction. Merely preparing or downloading a package never marks it as sent.

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

## E66 calculated-values evidence

The private `e66/1` adapter accepts original XML through
`POST /admin/vnb-calculated-values/<territory>/e66` with `Content-Type:
application/xml` and the existing `X-Admin-Token` credential. It uses the
`vnb-calculated-values/1` evidence ledger. The JSON endpoint cannot assert
`e66/1` validation by supplying a format label or metadata.

Before importing, configure each external metering-point ID in the existing
meter registry: internal community, confirmed participant building, external
VNB community ID and expected direction. Then post a JSON statement to
`/admin/vnb-calculated-values/<territory>/mappings/<metering_point_id>`:

```json
{
  "effective_from": "2026-01-01T00:00:00+01:00",
  "effective_until": null,
  "source_reference": "your documented membership agreement reference",
  "confirmed_membership": true
}
```

Dates must include an offset. `effective_until` is exclusive. The operator
attests that the current assignment covers this period; OpenLEG does not infer
historical membership or legal approval. The recorded principal is `admin-token`,
not a named reviewer. A changed community, building, external community ID or
direction clears the validity statement. Historical data outside a known current
assignment needs a separately verified mapping; it is refused rather than guessed.

Every point/direction needs all three quarter-hour kWh products throughout the
reported window: total `8716867000030`, exchanged LEG energy `2404050010123`,
and residual `2404050010124`. Only the delivered LEG channel supplies calculated
allocations. Totals alone are insufficient. Values must be finite, nonnegative
and exact multiples of 0.001 kWh below 100,000,000 kWh, the metering store's
range. Precision validation is independent of Decimal context and rejects
oversized exponents before expanding a rational value. Per interval, total minus residual minus local
may differ by at most 0.001 kWh, accounting for independently rounded channels.
This source-balance check does not define allocation-model or monthly tolerance.
The adapter currently accepts observations without a `Condition` code; coded
quality needs a reviewed source profile and is refused. Missing, repeated or
ambiguous identity/channel fields are refused. UTC interval coverage handles
Swiss days with 92 or 100 quarter hours.

Accepted evidence retains original bytes/hash, document ID, sender/receiver,
creation timestamp, exact channel quantities and precision, dated mapping
snapshot and correction lineage. These facts contribute to the replay
fingerprint. The draft freezes source metadata in its reconciliation; each
member's immutable invoice retains its own mapping and the source fingerprint.
Other participants' meter mappings stay private. Original-source replay uses
the frozen mapping and never reactivates withdrawn evidence.

Only accepted sources take that frozen replay path. Refused attempts return
HTTP 422 on repetition. Their registry-series hash and withdrawal lineage
distinguish a fresh attempt after the registry or overlapping evidence is
corrected; earlier refusals stay in history. Document IDs must fit the physical
import ledger's 64-character limit.

Document status `9` is an original. Status `5` requires a recorded withdrawal of
accepted evidence for the same window; use the existing withdrawal endpoint
before importing a replacement with a new document ID. Live overlapping
evidence is refused without changing physical readings. Status `1` does not
silently cancel anything: an operator must use the withdrawal flow. Reusing an
imported document ID for changed bytes is refused. Source, physical readings
and import ledger either commit together or all roll back on storage failure.

Billing still applies its existing reconciliation and approval gates; the
precision-aware allocation policy is tracked separately in #684. The supported
profile is covered by synthetic PostgreSQL tests. Representative privacy-safe
EWO samples are still needed before claiming EWO interoperability.

Source: [VSE SDAT 2025 §1.12, printed pp. 51–53](https://www.strom.ch/de/media/13628/download)
for the three products; [VSE Metering Code 2025 §6.1, printed p. 33](https://www.strom.ch/de/media/13608/download)
for quarter-hour energy precision. Neither document approves an OpenLEG-specific
allocation reconciliation policy.
