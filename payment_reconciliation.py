# SPDX-License-Identifier: AGPL-3.0-or-later
"""Parse ISO 20022 statements and submit canonical payments for reconciliation."""

from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from hashlib import sha256

from defusedxml import ElementTree

from translation_markers import gettext_noop as _N


class StatementError(ValueError):
    """The uploaded document is not a supported, valid bank statement."""


DECISION_LABELS = {
    "matched": _N("Automatisch verbucht"),
    "unmatched": _N("Keine passende Rechnung"),
    "ambiguous": _N("Mehrere mögliche Rechnungen"),
    "split_payment": _N("Teilzahlung"),
    "overpayment": _N("Überzahlung"),
    "reversal": _N("Rückbuchung"),
    "mismatch": _N("Angaben stimmen nicht überein"),
}


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
    if amount != stored_amount:
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
    statement_reference = _text(containers[0], "Id")
    entries = []
    scoped_entries = []
    for container in containers:
        account_currency = _account_currency(container)
        scoped_entries.extend(
            (entry, account_currency) for entry in container if _local(entry) == "Ntry"
        )
    for entry_number, (entry, account_currency) in enumerate(scoped_entries, start=1):
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
        reversal = _text(entry, "RvslInd").lower() == "true"
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
            entry_reference = _text(transaction, "AcctSvcrRef", "NtryRef")
            if not entry_reference:
                entry_reference = f"{statement_reference or 'statement'}:{entry_number}:{transaction_number}"
            elif len(transactions) > 1:
                entry_reference = f"{entry_reference}:{transaction_number}"
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
                )
            )
    if not entries:
        raise StatementError(_N("Der Kontoauszug enthält keine Zahlungseinträge."))
    return Statement(message_type, statement_reference, tuple(entries))


def import_statement(community_id, actor_id, source_name, content, store):
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
    )


def _normalised_reference(value):
    return "".join(
        character for character in str(value or "").upper() if character.isalnum()
    )


def match_payment(entry, invoices):
    """Classify one entry without mutating invoices or invoice snapshots."""
    reference = _normalised_reference(entry["payment_reference"])
    candidates = [
        invoice
        for invoice in invoices
        if reference and _normalised_reference(invoice["invoice_number"]) == reference
    ]
    invoice_id = candidates[0]["id"] if len(candidates) == 1 else None
    if entry["is_reversal"]:
        return MatchDecision("reversal", invoice_id)
    if len(candidates) > 1:
        return MatchDecision("ambiguous")
    if not candidates:
        return MatchDecision("unmatched")
    return MatchDecision(payment_eligibility(entry, candidates[0]), invoice_id)


def payment_eligibility(entry, invoice):
    """Classify acceptance rules shared by automatic and reviewed selection."""
    if entry["is_reversal"]:
        return "reversal"
    invoice_amount = Decimal(str(invoice["gross_chf"]))
    entry_amount = Decimal(str(entry["amount"]))
    if entry.get("booking_status") != "BOOK":
        return "mismatch"
    if entry.get("amount_basis") not in {"entry", "reconciled_batch"}:
        return "mismatch"
    if entry["credit_debit_indicator"] != "CRDT" or entry["currency"] != "CHF":
        return "mismatch"
    if entry_amount < invoice_amount:
        return "split_payment"
    if entry_amount > invoice_amount:
        return "overpayment"
    if invoice["lifecycle_state"] != "delivered":
        return "mismatch"
    return "matched"
