# SPDX-License-Identifier: AGPL-3.0-or-later
"""Deterministic fictional camt inputs bound to an existing village invoice."""

from decimal import Decimal
from xml.sax.saxutils import escape

KINDS = (
    "exact",
    "unknown-reference",
    "wrong-amount",
    "wrong-currency",
    "debit",
    "reversal",
)


def fixture(invoice, kind, message):
    """Describe an incoming fictional entry; never create a payment instruction."""
    if kind not in KINDS or message not in {"053", "054"}:
        raise ValueError("unknown camt fixture")
    amount = Decimal(str(invoice["gross_chf"]))
    if not amount.is_finite() or amount <= 0:
        raise ValueError("a positive invoice amount is required")
    if kind == "wrong-amount":
        amount += Decimal(1)
    reference = (
        "SIMULATION-UNKNOWN-REFERENCE"
        if kind == "unknown-reference"
        else invoice["invoice_number"]
    )
    identity = f"SIMULATION-SONNWIL-{invoice['id']}-{kind}-{message}"
    wrapper, container = (
        ("BkToCstmrStmt", "Stmt")
        if message == "053"
        else ("BkToCstmrDbtCdtNtfctn", "Ntfctn")
    )
    currency = "EUR" if kind == "wrong-currency" else "CHF"
    direction = "DBIT" if kind == "debit" else "CRDT"
    reversal = "true" if kind == "reversal" else "false"
    return (
        '<?xml version="1.0" encoding="UTF-8"?><!-- SIMULATION SONNWIL: no bank connection, no transfer instruction -->'
        f'<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.{message}.001.08">'
        f"<{wrapper}><{container}><Id>{escape(identity)}</Id>"
        "<Acct><Id><Othr><Id>SIMULATION-SONNWIL-NO-BANK</Id></Othr></Id></Acct>"
        f'<Ntry><Amt Ccy="{currency}">{amount:.2f}</Amt><CdtDbtInd>{direction}</CdtDbtInd><RvslInd>{reversal}</RvslInd>'
        "<Sts><Cd>BOOK</Cd></Sts><BookgDt><Dt>2026-11-06</Dt></BookgDt><NtryDtls><TxDtls><Refs>"
        f"<AcctSvcrRef>{escape(identity)}</AcctSvcrRef><EndToEndId>{escape(reference)}</EndToEndId>"
        f"</Refs><RmtInf><Ustrd>{escape(reference)}</Ustrd></RmtInf></TxDtls></NtryDtls></Ntry>"
        f"</{container}></{wrapper}></Document>"
    ).encode()
