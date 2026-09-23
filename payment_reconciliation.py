# SPDX-License-Identifier: AGPL-3.0-or-later
"""Parse ISO 20022 statements and submit canonical payments for reconciliation."""

from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from hashlib import sha256

from defusedxml import ElementTree


class StatementError(ValueError):
    """The uploaded document is not a supported, valid bank statement."""


DECISION_LABELS = {
    "matched": "Automatisch verbucht",
    "unmatched": "Keine passende Rechnung",
    "ambiguous": "Mehrere mögliche Rechnungen",
    "split_payment": "Teilzahlung",
    "overpayment": "Überzahlung",
    "reversal": "Rückbuchung",
    "mismatch": "Angaben stimmen nicht überein",
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


def _amount(element, fallback):
    candidates = _children(element, "Amt")
    amount_element = candidates[0] if candidates else fallback
    if amount_element is None:
        raise StatementError("Der Zahlungseintrag enthält keinen Betrag.")
    try:
        amount = Decimal((amount_element.text or "").strip())
    except InvalidOperation as error:
        raise StatementError(
            "Der Zahlungseintrag enthält keinen gültigen Betrag."
        ) from error
    if not amount.is_finite() or amount <= 0:
        raise StatementError("Der Zahlungseintrag enthält keinen gültigen Betrag.")
    currency = (amount_element.attrib.get("Ccy") or "").strip().upper()
    if len(currency) != 3:
        raise StatementError("Der Zahlungseintrag enthält keine gültige Währung.")
    return amount, currency


def parse_statement(content: bytes) -> Statement:
    """Parse camt.053 or camt.054 bytes into deterministic payment entries."""
    if not isinstance(content, bytes) or not content.strip():
        raise StatementError("Die Kontoauszugsdatei ist leer.")
    try:
        root = ElementTree.fromstring(content)
    except (ElementTree.ParseError, ValueError) as error:
        raise StatementError("Die Kontoauszugsdatei ist kein gültiges XML.") from error
    namespace = root.tag.split("}", 1)[0].lstrip("{") if "}" in root.tag else ""
    if "camt.053." in namespace:
        message_type, container_name = "camt.053", "Stmt"
    elif "camt.054." in namespace:
        message_type, container_name = "camt.054", "Ntfctn"
    else:
        raise StatementError("Unterstützt werden nur camt.053 und camt.054.")
    containers = _children(root, container_name)
    if not containers:
        raise StatementError("Der Kontoauszug enthält keinen Auszug.")
    statement_reference = _text(containers[0], "Id")
    entries = []
    for entry_number, entry in enumerate(_children(root, "Ntry"), start=1):
        fallback_amount = next(iter(_children(entry, "Amt")), None)
        booking_elements = _children(entry, "BookgDt")
        if not booking_elements:
            raise StatementError(
                "Der Zahlungseintrag enthält kein gültiges Buchungsdatum."
            )
        booking_text = _text(booking_elements[0], "Dt", "DtTm")
        try:
            booking_date = date.fromisoformat(booking_text[:10])
        except ValueError as error:
            raise StatementError(
                "Der Zahlungseintrag enthält kein gültiges Buchungsdatum."
            ) from error
        reversal = _text(entry, "RvslInd").lower() == "true"
        credit_debit_indicator = _text(entry, "CdtDbtInd").upper()
        if credit_debit_indicator not in {"CRDT", "DBIT"}:
            raise StatementError("Der Zahlungseintrag enthält keine gültige Richtung.")
        transactions = _children(entry, "TxDtls") or [entry]
        for transaction_number, transaction in enumerate(transactions, start=1):
            amount, currency = _amount(
                transaction, fallback_amount if len(transactions) == 1 else None
            )
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
                )
            )
    if not entries:
        raise StatementError("Der Kontoauszug enthält keine Zahlungseinträge.")
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
    invoice = candidates[0]
    invoice_amount = Decimal(str(invoice["gross_chf"]))
    entry_amount = Decimal(str(entry["amount"]))
    if entry["credit_debit_indicator"] != "CRDT" or entry["currency"] != "CHF":
        decision = "mismatch"
    elif entry_amount < invoice_amount:
        decision = "split_payment"
    elif entry_amount > invoice_amount:
        decision = "overpayment"
    elif invoice["lifecycle_state"] != "delivered":
        decision = "mismatch"
    else:
        decision = "matched"
    return MatchDecision(decision, invoice_id)
