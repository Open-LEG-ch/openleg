# Bank payment amounts

A single-transaction entry uses its direct `Ntry/Amt` amount and currency.
Amounts under `AmtDtls` describe instructions, interbank amounts, or values
before charges. They cannot replace the booked account credit. If a direct
`TxDtls/Amt` is supplied for that single transaction, it must agree with the
entry amount and currency. A conflicting transaction direction is rejected.

A declared batch must provide a valid `Btch/NbOfTxs` count matching its
transaction details. Incomplete breakdowns cannot use the entry total as one
payment. A batch requires one direct `TxDtls/Amt` per transaction. All amounts must use
the entry currency and sum exactly to `Ntry/Amt`. Batch charges and mixed
credit/debit directions are unsupported and cause the statement to be rejected.
A missing transaction amount never falls back to the batch total. These are
conservative supported cases, not a general currency-conversion policy.
Amounts must fit the existing two-decimal storage precision without rounding.

Imports persist `amount_basis` as `entry` or `reconciled_batch`. Automatic
matching and reviewed confirmation require this evidence. The schema upgrade
leaves old rows NULL and never repairs historical amounts or payment events.
Reviewable old entries require a new bank statement parsed by the fixed
importer. Replaying identical bytes returns the old import result and cannot
make its amounts trusted. Existing manual payment recording is unchanged.

The field meanings come from SIX's [Swiss Payment Standards 2025, sections
3.3 and the C/D-level amount definitions](https://www.six-group.com/dam/download/banking-services/standardization/sps/ig-cash-management-sps-2025-en.pdf).
`Ntry/Amt` is in account currency. `InstdAmt` is in instruction currency;
`TxAmt` may precede account conversion, and `CntrValAmt` precedes charges.

The synthetic conversion fixture `camt054_eur_credit.xml` validates against
ISO camt.054.001.08. It is not a bank-certified or full Swiss-profile fixture.
The [ISO archive](https://www.iso20022.org/catalogue-messages/iso-20022-messages-archive?page=1)
identifies the schema; the validation used its [Moov mirror](https://github.com/moov-io/iso20022/blob/master/docs/specifications/banktocustomer_cash_management_8/camt.054.001.08.xsd),
SHA-256 `2b392a1f7e70e70902fd0d803ff85989613bd1cae351663240b0bb9243be2c28`.
The repository does not download schemas during tests.
