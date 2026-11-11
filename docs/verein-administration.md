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
Contribution years renew on the original admission anniversary, with 28 February
used in non-leap years for a 29 February admission. The next leap year returns
to 29 February. The board's renewal action creates each due year once and queues
its own dated notice. The 30-day payment deadline belongs to that period.

A personally authenticated resignation is effective immediately on receipt;
no board approval or reason is required. Ended members do not renew. Their
accounting and decision records remain available. Mandatory fee corrections and
retention still need reviewed rules before actual intake. No automatic erasure
or unsupported waiver is implemented.

The schema upgrade also reconciles pending resignations saved by the former
board-resolution workflow. It preserves the declaration and original receipt
timestamp, applies that effective date and queues one receipt confirmation.
An already earlier membership end is retained. Repeated initialization does not
repeat the transition. Ending membership cancels unsent contribution notices
across every year. Delivery rechecks current eligibility while holding the same
membership lock as resignation and termination, including for an older fetched
batch. Sent correspondence remains in the history.

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

## Quarterly financial reports

`/verein/vorstand/finanzen` prepares immutable CHF reporting snapshots. This is
association reporting, separate from electricity billing, dues receipt matching,
payment authorisation and bank execution. Actual board approval of the complete
account/provider scope, sanitisation, quarterly cadence, evidence access and
independent review process must be referenced. Include all controlled accounts,
including dormant and provider accounts. Foreign-currency holdings or missing
sources prevent a claim of complete CHF coverage. No actual financial records
or balances are seeded by the application.

Paste the three CSV tables using the downloadable column templates. Use commas,
ISO dates and positive CHF amounts with a decimal point and at most two decimal
places, without grouping separators. Only completed calendar quarters are
accepted. Empty bookings and commitments use their header without data rows.
Each table is limited to 3,000 rows and 800,000 characters. Quoted CSV cells may
contain commas or line breaks. An invalid preparation retains the pasted input and identifies the affected
field and CSV row with a linked summary and inline error.

- `balances`: one row per `(account_id, fund)`. Account kinds are `bank` or
  `clearing`; funds are `general`, `reserve`, `restricted` or `held`. Include
  private statement references. `opening_chf` and `closing_chf` contain confirmed
  balances; unconfirmed provider payments go only in `pending_chf` on a clearing
  row. They never increase receipts or spendable funds.
- `bookings`: source IDs must be unique across the association, namespaced by
  actual source when providers reuse local IDs. Keep private statement/receipt
  references in `private_evidence`. Every booking has a date, positive amount,
  category, four public purposes and a redacted evidence code consisting of
  3–40 uppercase letters, digits or hyphens. These codes identify separately
  redacted evidence, never a raw statement, donor or account reference.
- `commitments`: record each outstanding unpaid commitment once, with its fund,
  positive amount, unique source reference and private evidence. Commitments
  remain separate from already paid expenses; the same source ID cannot appear
  in both bookings and unpaid commitments, including an already paid source
  from an earlier quarter. Publication rechecks this against the current history. Reconcile outstanding invoices
  against actual payments before preparing this table.

Receipt categories are `starting_contribution`, `membership`, `donation`,
`grant` and `held`. Accepted receipts require an actual private independence
assessment reference in `funding_check`; unknown or prohibited incoming money
remains in the held fund pending review or refund. Holding money does not mean
accepting its funding. Releasing held money through an allocation also requires
that review. Refunds use `refund`. Fees use `provider_fee` or `bank_fee`.
Expenses use `hosting`, `administration`, `community`, `development` or `other`.
Actual expenses remain reportable even if an approval failure needs investigation.
A source reference does not establish two-person spending approval.

A `transfer` uses category `internal`, a destination account and the same fund;
a provider's net payout is a transfer, never a second receipt. An `allocation`
also uses `internal` and changes fund within the same account. Its private
source must include the actual decision, for example a reserve release. Other
bookings leave the destination columns empty. External receipts add to the
source account/fund; refunds, fees, expenses and internal movements subtract.
Each closing account/fund balance must reconcile exactly. Internal movements
cancel from consolidated income and expenditure. Negative statement balances
are not supported by this first workflow; an overdraft needs a reviewed
accounting extension, rather than omission from the scope.

The public snapshot contains only dates, random stable booking IDs, CHF amounts,
kind/category, four reviewed purposes, redacted evidence codes, totals and fund
summaries. It contains no private account keys, source IDs, original evidence,
reviewer IDs or donor/member details. Free text, including a correction reason,
requires actual redaction review; field selection alone cannot anonymise prose.
Donor recognition is a separate opt-in process.

The preparer cannot publish their own report. A different currently authorised
person checks original sources, all-funds coverage, reconciliation and every
public field, then records actual review evidence. This publishes a fixed
version; it is not a statutory audit or annual review. Preparation alone is
never public. Public empty reports do not imply a zero balance, unavailable
storage returns 503, and a missing latest completed quarter is explicit.

Use the copy action to prepare a correction without editing an earlier snapshot.
Every new version needs a public reason. Source IDs keep stable public booking
IDs in the same quarter and cannot reappear in another quarter. Published source
rows cannot silently disappear. Prior published versions remain downloadable.
Publish consecutive quarters from the declared opening period. Opening balances
must match the preceding published closing balances. An earlier correction
cannot invalidate the next published quarter; such a balance restatement needs
a separately reviewed process covering every affected period. A correction
also cannot make a commitment in a later published report already paid. Genuine
unpaid commitments may carry forward until settled.

Available budget means confirmed general bank funds minus general unpaid
commitments. It excludes provider clearing, pending payments, reserve,
restricted and held funds. A negative value explicitly shows a shortfall.
The native worked fixture has CHF 4,000 opening bank cash, CHF 300 gross receipts,
CHF 20 refunded, CHF 2 fees and CHF 30 expenses, leaving CHF 4,248 cash. The
provider's CHF 98 payout and a CHF 100 bank transfer do not increase income.
CHF 50 pending is separate. With CHF 3,000 reserve, CHF 200 restricted and
CHF 120 general commitments, spendable bank funds are CHF 928. These values are
fictional test evidence, not actual association balances.

## Bank transfer and Swiss QR donations

`/verein/vorstand/zahlungsempfaenger` prepares a recipient for independent review.
Use the Verein's actual ordinary CH/LI IBAN and bank-confirmed account holder,
public structured address and unstructured donation message. Keep bank ownership
and address-publication evidence private. This route supports CHF with `NON`,
without a structured reference; QR-IBANs are refused. It does not collect bank
credentials, open an account, execute payments or create membership.

A private QR draft lets the second reviewer check the exact generated data with
the bank before activation. Its PDF is marked as a draft. Actual QR validation
and independent approval evidence are required at approval, so missing QR proof
does not prevent preparing a draft for that validation. An IBAN checksum does
not establish account ownership. Newly prepared versions do not change existing
instructions; only a different active reviewer can approve the latest version.

`/verein/spenden` displays readable recipient details and an optional amount.
Payment information also requires actual formation and configured controller,
contact address and privacy version. Without these facts or an approved active
recipient, no account or QR is exposed. No production fixture is created.
Changes require a new reviewed version. Withdrawing the current recipient needs
a reason and immediately removes collection instructions; older accounts never
reactivate automatically. Stale file URLs cannot silently select a newer account.
Pages and files use no-store. Already saved payment documents are outside cache
revocation; communicate actual account changes through the approved process.

Browser success/return parameters do not establish payment, settle dues or admit
members. Match actual incoming source records separately. Recognition of donors
requires separate opt-in; financial reporting uses redacted sources. This route
makes no tax-deductibility claim. Mandatory TWINT onboarding and settlement are
separate launch dependencies; these bank instructions do not authorise a partial
bank-only public launch.

The generator follows the CHF subset of [SIX IG 2.4](https://www.six-group.com/dam/download/banking-services/standardization/qr-bill/ig-qr-bill-v2.4-en.pdf),
which leaves CHF technical requirements unchanged from 2.3. It uses restricted
UTF-8, binary QR encoding at level M, a 46 mm symbol, a 7 mm SIX recognition mark
and a 5 mm quiet border. Dense payloads that cannot retain the minimum module
size are refused. Online instructions show readable data; the A4 PDF places the
210 × 105 mm payment part and receipt at the bottom, with cut guidance and blank
payer/amount fields where needed. Use the prescribed four-language headings and
permitted Liberation Sans font. Independent decoding and actual bank validation
remain distinct checks. No real transfer is initiated by preparing these files.
