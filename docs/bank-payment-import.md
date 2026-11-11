# Bank payment import

Bank-statement imports and reviewed bank-payment confirmation share payment
eligibility rules. A bank entry needs explicit `BOOK` status, a CHF credit,
no reversal, an exact amount, and a delivered invoice before it can mark the
invoice paid. Automatic selection also needs a unique matching reference.

The parser accepts the older direct `Ntry/Sts` code and the newer
`Ntry/Sts/Cd` representation. Duplicate or mixed status elements do not establish
booking. Missing, proprietary, pending, informational, and unknown statuses
cannot qualify as booked. Entries with the other required fields remain in the
import audit, but matching or confirmation refuses payment. The importer still
requires a booking date; a pending entry without one is rejected as before.

The schema upgrade adds nullable booking status without backfilling it. Existing
rows remain unknown, including entries that an earlier import already matched.
This change does not rewrite historical payment events or invoice snapshots.
Reviewable old entries cannot be confirmed without booking evidence. Import a
new bank statement that explicitly reports the booked credit. Replaying the
identical old file returns its existing import result and does not upgrade its
evidence. Do not edit an old entry to invent booking status.

SIX's [Swiss Payment Standards 2025, cash-management guidelines, pages 64-65](https://www.six-group.com/dam/download/banking-services/standardization/sps/ig-cash-management-sps-2025-en.pdf)
define `Ntry/Sts/Cd`, with `BOOK` for booked entries and `PDNG` for pending
entries. camt.054 may contain either. The legacy direct-code form is retained
for older CAMT deliveries; both forms require the exact code `BOOK`.

[Sage's CAMT examples, annex 2](https://fr-kb.sage.com/portal/app/portlets/results/viewdocument.jsp?solutionid=250415092149400)
show the direct `Sts` code in camt.053.001.04. Parser regressions exercise both
status forms with .04 and .08 namespaces, without claiming full XSD validation.
