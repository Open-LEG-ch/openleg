# SPDX-License-Identifier: AGPL-3.0-or-later
"""Parse ISO 20022 statements and submit canonical payments for reconciliation."""

import json
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from hashlib import sha256

from defusedxml import ElementTree

from billing_lifecycle import InvoiceLifecycleError
from money_display import InvalidMoneyAmount, finite_decimal
from operator_errors import ErrorCode
from translation_markers import gettext_noop as _N


class StatementError(ValueError):
    """The uploaded document is not a supported, valid bank statement."""


class PaymentMatchRefused(InvoiceLifecycleError):
    """A review failed shared payment eligibility."""

    def __init__(self, detail):
        super().__init__("Die Zahlung passt nicht exakt zur Rechnung.")
        self.detail = detail
        self.reason = {
            "invalid_invoice_total": ErrorCode.INVALID_INVOICE_TOTAL,
            "missing_reference": ErrorCode.MISSING_REFERENCE,
        }.get(detail, ErrorCode.INVALID_PAYMENT)


class BankPaymentDuplicate(InvoiceLifecycleError):
    """A statement entry needs human review of plausible duplicate payments."""

    reason = ErrorCode.POSSIBLE_DUPLICATE_PAYMENT

    def __init__(self, matches, *, entry):
        super().__init__("Bank credit needs duplicate review")
        self.matches = matches
        self.entry = entry

    @property
    def review(self):
        return sorted([row["id"], row["booked_invoice_id"]] for row in self.matches)


DECISION_LABELS = {
    "matched": _N("Automatisch verbucht"),
    "unmatched": _N("Keine passende Rechnung"),
    "ambiguous": _N("Mehrere mögliche Rechnungen"),
    "split_payment": _N("Teilzahlung"),
    "overpayment": _N("Überzahlung"),
    "reversal": _N("Rückbuchung"),
    "mismatch": _N("Angaben stimmen nicht überein"),
}
REVIEWABLE_DECISIONS = frozenset({"unmatched", "ambiguous", "mismatch"})


@dataclass(frozen=True)
class PaymentEntry:
    entry_reference: str
    booking_date: date
    amount: Decimal
    currency: str
    payment_reference: str
    is_reversal: bool
    credit_debit_indicator: str
    booking_status: str | None = None
    amount_basis: str | None = None
    debtor_name: str | None = None
    remittance_information: str | None = None
    bank_references: tuple[str, ...] = ()
    creditor_references: tuple[str, ...] = ()
    source_statement_reference: str | None = None
    bank_account: str = ""
    servicer_reference: str | None = None


@dataclass(frozen=True)
class Statement:
    message_type: str
    statement_reference: str
    entries: tuple[PaymentEntry, ...]


@dataclass(frozen=True)
class MatchDecision:
    decision: str
    invoice_id: int | None = None


def _local(element):
    return element.tag.rsplit("}", 1)[-1]


def _children(element, name):
    return [item for item in element.iter() if _local(item) == name]


def _text(element, *names):
    for name in names:
        for item in _children(element, name):
            value = (item.text or "").strip()
            if value and value != "NOTPROVIDED":
                return value
    return ""


def _booking_status(entry):
    """Read the entry's direct status, never a nested transaction's status.

    Older CAMT uses Sts text; newer CAMT uses the Sts/Cd choice. A missing,
    proprietary, duplicate or mixed representation is not booking evidence.
    """
    statuses = [child for child in entry if _local(child) == "Sts"]
    if len(statuses) != 1:
        return None
    status = statuses[0]
    if not list(status):
        return (status.text or "").strip() or None
    if (status.text or "").strip() or len(status) != 1:
        return None
    code = status[0]
    if _local(code) != "Cd" or list(code) or (code.tail or "").strip():
        return None
    return (code.text or "").strip() or None


def _account_currency(container):
    accounts = [child for child in container if _local(child) == "Acct"]
    if not accounts:
        return None
    if len(accounts) != 1:
        raise StatementError(_N("Der Zahlungseintrag enthält keine gültige Währung."))
    currencies = [child for child in accounts[0] if _local(child) == "Ccy"]
    if not currencies:
        return None
    value = (currencies[0].text or "").strip().upper()
    if (
        len(currencies) != 1
        or len(value) != 3
        or not value.isascii()
        or not value.isalpha()
    ):
        raise StatementError(_N("Der Zahlungseintrag enthält keine gültige Währung."))
    return value


def _account_details(element, path):
    """Return every non-empty leaf below element as (path, text) pairs."""
    children = list(element)
    if children:
        details = []
        for child in children:
            # Older camt versions name the servicer's BIC "BIC" instead of "BICFI".
            name = "BICFI" if _local(child) == "BIC" else _local(child)
            details.extend(_account_details(child, f"{path}/{name}"))
        return details
    value = (element.text or "").strip()
    # An 11-character BIC ending in XXX names the same bank as its 8-character form.
    if path.endswith("/BICFI") and len(value) == 11 and value.endswith("XXX"):
        value = value[:8]
    return [(path, value)] if value else []


def _bank_account(container):
    """Identify the statement account by its IBAN or a typed proprietary ID.

    A proprietary ID is unique only together with its scheme, issuer and
    servicing bank, so the key covers every reported detail of those elements.
    Servicer names and addresses are unbounded, so the indexed key is a digest.
    """
    iban = container.findtext("./{*}Acct/{*}Id/{*}IBAN", default="").strip()
    if iban:
        return iban
    if not container.findtext("./{*}Acct/{*}Id/{*}Othr/{*}Id", default="").strip():
        return ""
    details = sorted(
        detail
        for name, path in (
            ("Othr", "./{*}Acct/{*}Id/{*}Othr"),
            ("Svcr", "./{*}Acct/{*}Svcr"),
        )
        for element in container.findall(path)
        for detail in _account_details(element, name)
    )
    canonical = json.dumps(details, ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + sha256(canonical.encode()).hexdigest()


def _bank_references(element, *paths):
    return tuple(
        node.text
        for path in paths
        for node in element.findall(path)
        if (trimmed := (node.text or "").strip()) and trimmed.upper() != "NOTPROVIDED"
    )


def _creditor_references(transaction):
    return tuple(
        dict.fromkeys(
            raw
            for node in transaction.findall("./{*}RmtInf/{*}Strd/{*}CdtrRefInf/{*}Ref")
            if (raw := (node.text or "").strip()) and raw.upper() != "NOTPROVIDED"
        )
    )


def _servicer_reference(transaction, entry, transaction_count):
    """Return the bank's AcctSvcrRef for this credit, never a shared batch value."""
    paths = (
        [(transaction, "./{*}Refs/{*}AcctSvcrRef")] if transaction is not entry else []
    )
    if transaction_count == 1:
        paths.append((entry, "./{*}AcctSvcrRef"))
    for element, path in paths:
        values = {value.strip() for value in _bank_references(element, path)}
        if values:
            return values.pop() if len(values) == 1 else None
    return None


def _debtor_name(transaction):
    debtor = transaction.find("./{*}RltdPties/{*}Dbtr")
    if debtor is not None:
        for path in ("./{*}Nm", "./{*}Pty/{*}Nm"):
            name = debtor.findtext(path, default="").strip()
            if name and name != "NOTPROVIDED":
                return name
    return None


def _validate_batch_details(entry, entry_direction):
    """Do not mistake an incomplete batch's total for an individual payment."""
    declared = False
    for group in entry:
        if _local(group) != "NtryDtls":
            continue
        batches = [child for child in group if _local(child) == "Btch"]
        if not batches:
            continue
        declared = True
        directions = [
            child
            for batch in batches
            for child in batch
            if _local(child) == "CdtDbtInd"
        ]
        if directions and (
            len(directions) != 1
            or (directions[0].text or "").strip() != entry_direction
        ):
            raise StatementError(
                _N("Der Zahlungseintrag enthält keine gültige Richtung.")
            )
        counts = [
            child for batch in batches for child in batch if _local(child) == "NbOfTxs"
        ]
        text = (counts[0].text or "").strip() if len(counts) == 1 else ""
        transactions = [child for child in group if _local(child) == "TxDtls"]
        if (
            len(batches) != 1
            or not text.isascii()
            or not text.isdigit()
            or len(text) > 15
            or int(text) == 0
            or int(text) != len(transactions)
        ):
            raise StatementError(
                _N("Der Zahlungseintrag enthält keinen gültigen Betrag.")
            )

        totals = [child for child in batches[0] if _local(child) == "TtlAmt"]
        if totals:
            total_amount, total_currency = _amount(batches[0], "TtlAmt")
            amounts = [_amount(transaction) for transaction in transactions]
            if (
                any(currency != total_currency for _, currency in amounts)
                or sum(amount for amount, _ in amounts) != total_amount
            ):
                raise StatementError(
                    _N("Der Zahlungseintrag enthält keinen gültigen Betrag.")
                )
    return declared


def _amount(element, tag="Amt"):
    candidates = [child for child in element if _local(child) == tag]
    if len(candidates) != 1:
        raise StatementError(_N("Der Zahlungseintrag enthält keinen Betrag."))
    amount_element = candidates[0]
    try:
        amount = Decimal((amount_element.text or "").strip())
    except InvalidOperation as error:
        raise StatementError(
            _N("Der Zahlungseintrag enthält keinen gültigen Betrag.")
        ) from error
    if not amount.is_finite() or amount <= 0:
        raise StatementError(_N("Der Zahlungseintrag enthält keinen gültigen Betrag."))
    currency = (amount_element.attrib.get("Ccy") or "").strip().upper()
    if len(currency) != 3:
        raise StatementError(_N("Der Zahlungseintrag enthält keine gültige Währung."))
    try:
        stored_amount = amount.quantize(Decimal("0.01"))
    except InvalidOperation as error:
        raise StatementError(
            _N("Der Zahlungseintrag enthält keinen gültigen Betrag.")
        ) from error
    if amount != stored_amount or amount >= Decimal(1000000000000):
        raise StatementError(_N("Der Zahlungseintrag enthält keinen gültigen Betrag."))
    return amount, currency


def parse_statement(content: bytes) -> Statement:
    """Parse camt.053 or camt.054 bytes into deterministic payment entries."""
    if not isinstance(content, bytes) or not content.strip():
        raise StatementError(_N("Die Kontoauszugsdatei ist leer."))
    try:
        root = ElementTree.fromstring(content)
    except (ElementTree.ParseError, ValueError) as error:
        raise StatementError(
            _N("Die Kontoauszugsdatei ist kein gültiges XML.")
        ) from error
    namespace = root.tag.split("}", 1)[0].lstrip("{") if "}" in root.tag else ""
    if "camt.053." in namespace:
        message_type, container_name = "camt.053", "Stmt"
    elif "camt.054." in namespace:
        message_type, container_name = "camt.054", "Ntfctn"
    else:
        raise StatementError(_N("Unterstützt werden nur camt.053 und camt.054."))
    containers = _children(root, container_name)
    if not containers:
        raise StatementError(_N("Der Kontoauszug enthält keinen Auszug."))
    statement_reference = containers[0].findtext("./{*}Id", default="").strip()
    entries = []
    scoped_entries = []
    seen_references = set()
    for container in containers:
        account_currency = _account_currency(container)
        source_statement = container.findtext("./{*}Id", default="").strip()
        bank_account = _bank_account(container)
        scoped_entries.extend(
            (entry, account_currency, source_statement, bank_account)
            for entry in container
            if _local(entry) == "Ntry"
        )
    for entry_number, (
        entry,
        account_currency,
        source_statement,
        bank_account,
    ) in enumerate(scoped_entries, start=1):
        booking_elements = _children(entry, "BookgDt")
        if not booking_elements:
            raise StatementError(
                _N("Der Zahlungseintrag enthält kein gültiges Buchungsdatum.")
            )
        booking_text = _text(booking_elements[0], "Dt", "DtTm")
        try:
            booking_date = date.fromisoformat(booking_text[:10])
        except ValueError as error:
            raise StatementError(
                _N("Der Zahlungseintrag enthält kein gültiges Buchungsdatum.")
            ) from error
        reversal = _text(entry, "RvslInd").lower() in {"true", "1"}
        credit_debit_indicator = _text(entry, "CdtDbtInd").upper()
        if credit_debit_indicator not in {"CRDT", "DBIT"}:
            raise StatementError(
                _N("Der Zahlungseintrag enthält keine gültige Richtung.")
            )
        declared_batch = _validate_batch_details(entry, credit_debit_indicator)
        transactions = _children(entry, "TxDtls") or [entry]
        is_batch = declared_batch or len(transactions) > 1
        booked_amount, booked_currency = _amount(entry)
        if account_currency is not None and account_currency != booked_currency:
            raise StatementError(
                _N("Der Zahlungseintrag enthält keine gültige Währung.")
            )
        for transaction in transactions:
            directions = [
                child for child in transaction if _local(child) == "CdtDbtInd"
            ]
            if directions and (
                len(directions) != 1
                or (directions[0].text or "").strip() != credit_debit_indicator
            ):
                raise StatementError(
                    _N("Der Zahlungseintrag enthält keine gültige Richtung.")
                )
            if not is_batch and transaction is not entry:
                direct_amounts = [
                    child for child in transaction if _local(child) == "Amt"
                ]
                if direct_amounts and _amount(transaction) != (
                    booked_amount,
                    booked_currency,
                ):
                    raise StatementError(
                        _N("Der Zahlungseintrag enthält keinen gültigen Betrag.")
                    )
        if is_batch:
            details = [_amount(transaction) for transaction in transactions]
            if (
                any(currency != booked_currency for _, currency in details)
                or sum(amount for amount, _ in details) != booked_amount
                or _children(entry, "Chrgs")
                or _children(entry, "ChrgsInf")
            ):
                raise StatementError(
                    _N("Der Zahlungseintrag enthält keinen gültigen Betrag.")
                )
        for transaction_number, transaction in enumerate(transactions, start=1):
            if not is_batch:
                amount, currency = _amount(entry)
            else:
                amount, currency = _amount(transaction)
            payment_reference = _text(transaction, "Ref", "Ustrd", "EndToEndId")
            transaction_references = (
                _bank_references(
                    transaction,
                    "./{*}Refs/{*}AcctSvcrRef",
                    "./{*}Refs/{*}NtryRef",
                    "./{*}AcctSvcrRef",
                    "./{*}NtryRef",
                )
                if transaction is not entry
                else ()
            )
            entry_references = _bank_references(
                entry, "./{*}AcctSvcrRef", "./{*}NtryRef"
            )
            references = tuple(
                dict.fromkeys((*transaction_references, *entry_references))
            )
            remittance_lines = transaction.findall("./{*}RmtInf/{*}Ustrd")
            remittance_lines.extend(
                transaction.findall("./{*}RmtInf/{*}Strd/{*}AddtlRmtInf")
            )
            remittance = "\n".join(
                line for item in remittance_lines if (line := (item.text or "").strip())
            )
            entry_reference = _text(transaction, "AcctSvcrRef", "NtryRef")
            if not entry_reference:
                entry_reference = f"{source_statement or 'statement'}:{entry_number}:{transaction_number}"
            elif len(transactions) > 1:
                entry_reference = f"{entry_reference}:{transaction_number}"
            # Bank-local references are unique per account. Suffix only a reference
            # one account reuses; bank_references keeps the original values.
            while (bank_account, entry_reference) in seen_references:
                entry_reference += f":{entry_number}:{transaction_number}"
            seen_references.add((bank_account, entry_reference))
            entries.append(
                PaymentEntry(
                    entry_reference=entry_reference,
                    booking_date=booking_date,
                    amount=amount,
                    currency=currency,
                    payment_reference=payment_reference,
                    is_reversal=reversal,
                    credit_debit_indicator=credit_debit_indicator,
                    booking_status=_booking_status(entry),
                    amount_basis="reconciled_batch" if is_batch else "entry",
                    debtor_name=_debtor_name(transaction),
                    remittance_information=remittance or None,
                    bank_references=references,
                    creditor_references=_creditor_references(transaction),
                    source_statement_reference=source_statement or None,
                    bank_account=bank_account,
                    servicer_reference=_servicer_reference(
                        transaction, entry, len(transactions)
                    ),
                )
            )
    if not entries:
        raise StatementError(_N("Der Kontoauszug enthält keine Zahlungseinträge."))
    return Statement(message_type, statement_reference, tuple(entries))


def import_statement(
    community_id, actor_id, source_name, content, store, *, upload_id=None
):
    """Parse and atomically reconcile a statement through the billing store seam."""
    statement = parse_statement(content)
    return store.reconcile_bank_statement(
        community_id=community_id,
        actor_id=actor_id,
        source_name=source_name,
        message_type=statement.message_type,
        statement_reference=statement.statement_reference,
        fingerprint=sha256(content).hexdigest(),
        entries=[asdict(entry) for entry in statement.entries],
        upload_id=upload_id,
    )


# The characters str.strip() removes, spelled out so SQL btrim() trims the same set.
BANK_REFERENCE_WHITESPACE = (
    "\t\n\v\f\r\x1c\x1d\x1e\x1f \x85\xa0\u1680\u2000\u2001\u2002\u2003\u2004"
    "\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000"
)


def bank_reference_key(reference):
    return reference.strip(BANK_REFERENCE_WHITESPACE)


def normalise_payment_reference(value):
    return "".join(
        character for character in str(value or "").upper() if character.isalnum()
    )


def match_payment(entry, invoices):
    """Classify one entry without mutating invoices or invoice snapshots."""
    reference = normalise_payment_reference(entry["payment_reference"])
    candidates = [
        invoice
        for invoice in invoices
        if reference
        and normalise_payment_reference(invoice["invoice_number"]) == reference
    ]
    invoice_id = candidates[0]["id"] if len(candidates) == 1 else None
    if entry["is_reversal"]:
        return MatchDecision("reversal", invoice_id)
    if len(candidates) > 1:
        return MatchDecision("ambiguous")
    if not candidates:
        return MatchDecision("unmatched")
    try:
        decision = payment_eligibility(entry, candidates[0])
    except PaymentMatchRefused:
        decision = "mismatch"
    return MatchDecision(decision, invoice_id)


def payment_entry_refusal(entry):
    """Explain restrictions that apply before an invoice is selected."""
    if entry["is_reversal"]:
        return "reversal"
    if entry.get("booking_status") != "BOOK":
        return "booking_status"
    if entry.get("amount_basis") not in {"entry", "reconciled_batch"}:
        return "amount_basis"
    if entry["credit_debit_indicator"] != "CRDT":
        return "debit"
    if entry["currency"] != "CHF":
        return "currency"
    if not _has_text(entry.get("payment_reference")) and not _has_text(
        entry.get("servicer_reference")
    ):
        return "missing_reference"
    return None


def _has_text(value):
    return isinstance(value, str) and bool(value.strip())


def audit_reference(entry):
    """Return the invoice audit reference and the CAMT element standing in for it.

    Only a person in the billing workspace may book a credit through the stand-in.
    """
    if _has_text(entry.get("payment_reference")):
        return entry["payment_reference"], None
    return entry.get("servicer_reference"), "AcctSvcrRef"


def is_duplicate_payment_evidence(entry):
    """A booked payment or a statement entry itself eligible for booking."""
    return entry["match_decision"] == "matched" or payment_entry_refusal(entry) is None


def payment_eligibility(entry, invoice):
    """Classify acceptance rules shared by automatic and reviewed selection."""
    refusal = payment_entry_refusal(entry)
    if refusal:
        return "reversal" if refusal == "reversal" else "mismatch"
    try:
        invoice_amount = finite_decimal(invoice.get("gross_chf"))
    except InvalidMoneyAmount as exc:
        raise PaymentMatchRefused("invalid_invoice_total") from exc
    entry_amount = Decimal(str(entry["amount"]))
    if entry_amount < invoice_amount:
        return "split_payment"
    if entry_amount > invoice_amount:
        return "overpayment"
    if invoice["lifecycle_state"] != "delivered":
        return "mismatch"
    return "matched"
