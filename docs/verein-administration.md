# Association administration

Association membership is separate from a LEG participant, dashboard account,
donor, contributor or event attendee. Hosted-product operation is outside the
association's intended remit. The complete site and intake are disabled by
default; private implementation does not establish formation or adoption.

The public page and intake share `site_release.accepting_applications`. It needs
an actual formation date, the intake switch, contact address, statutes link,
privacy version and controller. Configure these only from reviewed actual
records. Closing new admissions keeps existing personal access available.

Provision individual board accounts with `flask --app app:create_app
verein-add-reviewer` and revoke departing people with `verein-revoke-reviewer`.
Record the actual election or delegation separately. There is no anonymous role
signup. Sessions expire and check current account revocation on every operation.
Passwords and personal access links must never enter public issues or logs.

## Membership

Applications require confirmation, then a private board decision. Admission
creates CHF 50 dues payable after 30 days. A reminder starts the further 60-day
period only after actual successful delivery. Termination is a reasoned board
decision. A successful appeal invalidates the challenged reminder period while
retaining its sent history. A fresh successful reminder starts a new period.

Record receipts against actual source references. Partial payments remain
outstanding. Duplicate references cannot inflate receipts. A receipt does not
approve spending, prove provider settlement or reactivate ended membership.
Annual renewal, resignation effect and retention require reviewed adopted rules
before intake is activated. No automatic deletion or unsupported waiver is
implemented.

## Meetups

`/verein/vorstand/treffen` uses the same individually authenticated board
accounts. Prepare every actual occurrence separately; there is no generated
recurring promise. Enter public titles, audience and agenda in all four languages,
the actual organiser, spoken languages and Europe/Zurich times. Invalid and
ambiguous wall-clock times are refused. The join link must be a public HTTPS
registration route. Keep meeting passwords and private invitation URLs outside
this public-content editor.

Drafts are private. Published occurrences retain their URL when moved or
cancelled and cannot return to draft. Version checks refuse stale edits. Publish
notes only after the occurrence and record the actual approval reference.
Withdrawing notes immediately removes them from the public page; private version
history remains. There is no recording upload or archive.

Calendar files use stable UIDs, increasing sequence values, UTC instants and
cancelled status. They include only the public detail URL, without joining links,
approval references or notes. Text escaping and UTF-8 line folding follow
[RFC 5545](https://www.rfc-editor.org/rfc/rfc5545).

Native-language review and real dates/people remain release prerequisites.
Fixtures must never be imported as actual public events.
