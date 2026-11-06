# SPDX-License-Identifier: AGPL-3.0-or-later
"""CHF report reconciliation. Source records and policy adoption stay private."""

import calendar
import csv
import io
import re
from collections import defaultdict
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from i18n import gettext as _

LOCALES = ("de_CH", "en", "fr_CH", "it_CH")
FUNDS = ("general", "reserve", "restricted", "held")
COLUMNS = {
    "balances": "account_id,kind,fund,opening_chf,closing_chf,pending_chf,private_evidence",
    "bookings": "source_id,date,kind,account_id,fund,to_account_id,to_fund,amount_chf,category,purpose_de_CH,purpose_en,purpose_fr_CH,purpose_it_CH,evidence_code,private_evidence,funding_check",
    "commitments": "source_id,fund,amount_chf,private_evidence",
}
KINDS = {
    "receipt": "gross_receipts_cents",
    "refund": "refunds_cents",
    "fee": "fees_cents",
    "expense": "expenses_cents",
    "transfer": None,
    "allocation": None,
}
CATEGORIES = {
    "receipt": ("starting_contribution", "membership", "donation", "grant", "held"),
    "refund": ("refund",),
    "fee": ("provider_fee", "bank_fee"),
    "expense": ("hosting", "administration", "community", "development", "other"),
    "transfer": ("internal",),
    "allocation": ("internal",),
}


class FinanceInputError(ValueError):
    """An incomplete, malformed or unreconciled report cannot be prepared."""

    def __init__(self, message, *, field=None, row=None):
        super().__init__(message)
        self.field = field
        self.row = row


@contextmanager
def _location(field, row=None):
    try:
        yield
    except FinanceInputError as exc:
        if exc.field is None:
            exc.field, exc.row = field, row
        raise


def text(value, *, required=True, limit=500):
    if not isinstance(value, str) or len(value) > limit or "\x00" in value:
        raise FinanceInputError(
            _("Der Text ist zu lang oder enthält ungültige Zeichen.")
        )
    value = value.strip()
    if required and not value:
        raise FinanceInputError(_("Diese Angabe ist erforderlich."))
    return value


def _rows(value, name):
    value = text(value, limit=800000)
    try:
        reader = csv.DictReader(io.StringIO(value), strict=True)
        if reader.fieldnames != COLUMNS[name].split(","):
            raise FinanceInputError(
                _(
                    "Verwenden Sie die Spaltenüberschriften der CSV-Vorlage in derselben Reihenfolge."
                )
            )
        rows = []
        for row in reader:
            if len(rows) >= 3000 or None in row or any(v is None for v in row.values()):
                raise FinanceInputError(
                    _(
                        "Die CSV-Zeile ist unvollständig oder die Tabelle überschreitet 3’000 Zeilen."
                    ),
                    field=name,
                    row=reader.line_num,
                )
            with _location(name, reader.line_num):
                rows.append(
                    {
                        key: text(val, required=False, limit=1000)
                        for key, val in row.items()
                    }
                )
        return rows
    except csv.Error as exc:
        raise FinanceInputError(
            _(
                "Die CSV-Schreibweise ist ungültig. Prüfen Sie Anführungszeichen und Trennzeichen."
            ),
            field=name,
            row=reader.line_num,
        ) from exc


def _cents(value, *, positive=False):
    if not re.fullmatch(r"(?:0|[1-9][0-9]{0,8})(?:\.[0-9]{1,2})?", value):
        raise FinanceInputError(
            _(
                "Verwenden Sie nichtnegative CHF-Beträge mit Punkt und höchstens zwei Nachkommastellen."
            )
        )
    whole, _separator, fraction = value.partition(".")
    amount = int(whole) * 100 + int(fraction.ljust(2, "0"))
    if positive and not amount:
        raise FinanceInputError(_("Der Buchungsbetrag muss grösser als null sein."))
    return amount


def reconcile(data):
    with _location("quarter"):
        quarter = text(data.get("quarter", ""))
        if not re.fullmatch(r"20[0-9]{2}-Q[1-4]", quarter):
            raise FinanceInputError(
                _("Geben Sie ein Kalenderquartal im Format 2026-Q3 an.")
            )
        year, number = int(quarter[:4]), int(quarter[-1])
        month = number * 3
        start = f"{year}-{month - 2:02d}-01"
        end = f"{year}-{month:02d}-{calendar.monthrange(year, month)[1]}"
        if end >= datetime.now(ZoneInfo("Europe/Zurich")).date().isoformat():
            raise FinanceInputError(
                _("Nur abgeschlossene Kalenderquartale sind zulässig.")
            )
    private = {}
    for name in ("scope_evidence", "opening_evidence"):
        with _location(name):
            private[name] = text(data.get(name, ""))
    with _location("all_funds"):
        if data.get("all_funds") != "yes":
            raise FinanceInputError(
                _(
                    "Bestätigen Sie den vollständigen Konten- und Anbieterumfang ohne fehlende Fremdwährungsbestände."
                )
            )
    for name in COLUMNS:
        with _location(name):
            private[name] = _rows(data.get(name, ""), name)
    if not private["balances"]:
        raise FinanceInputError(
            _("Erfassen Sie alle kontrollierten Konten und Anbieter."), field="balances"
        )
    accounts, balances, changes = {}, {}, defaultdict(int)
    totals = dict.fromkeys(
        (
            "opening_bank_cents",
            "closing_bank_cents",
            "opening_clearing_cents",
            "closing_clearing_cents",
            "pending_cents",
            "reserve_cents",
            "restricted_cents",
            "held_cents",
            "commitments_cents",
            "spendable_bank_cents",
            "gross_receipts_cents",
            "refunds_cents",
            "fees_cents",
            "expenses_cents",
        ),
        0,
    )
    funds = {
        fund: {
            "bank_cents": 0,
            "clearing_cents": 0,
            "pending_cents": 0,
            "commitments_cents": 0,
        }
        for fund in FUNDS
    }
    for row_number, row in enumerate(private["balances"], start=2):
        with _location("balances", row_number):
            account, fund, kind = row["account_id"], row["fund"], row["kind"]
            key = (account, fund)
            if (
                not account
                or fund not in FUNDS
                or kind not in ("bank", "clearing")
                or key in balances
                or accounts.get(account, kind) != kind
                or not row["private_evidence"]
            ):
                raise FinanceInputError(
                    _(
                        "Prüfen Sie Kontoart, Fonds und private Quellenreferenz. Jede Konto-Fonds-Kombination darf nur einmal vorkommen."
                    )
                )
            accounts[account] = kind
            opening, closing, pending = (
                _cents(row[name])
                for name in ("opening_chf", "closing_chf", "pending_chf")
            )
            if pending and kind != "clearing":
                raise FinanceInputError(
                    _(
                        "Unbestätigte Zahlungen gehören ausschliesslich in pending_chf eines Anbieterkontos vom Typ clearing."
                    )
                )
            balances[key] = (opening, closing, row_number)
            totals[f"opening_{kind}_cents"] += opening
            totals[f"closing_{kind}_cents"] += closing
            totals["pending_cents"] += pending
            funds[fund][f"{kind}_cents"] += closing
            funds[fund]["pending_cents"] += pending
            if fund != "general":
                totals[f"{fund}_cents"] += closing
    public_bookings, sources = [], set()
    for row_number, row in enumerate(private["bookings"], start=2):
        with _location("bookings", row_number):
            source, kind = row["source_id"], row["kind"]
            key = (row["account_id"], row["fund"])
            amount = _cents(row["amount_chf"], positive=True)
            if (
                not source
                or source in sources
                or kind not in KINDS
                or key not in balances
                or not row["private_evidence"]
            ):
                raise FinanceInputError(
                    _(
                        "Prüfen Sie Quellkennung, Buchungsart, Konto, Fonds und Quellenreferenz. Quellkennungen dürfen nicht doppelt vorkommen."
                    )
                )
            sources.add(source)
            try:
                parsed_date = date.fromisoformat(row["date"]).isoformat()
            except ValueError as exc:
                raise FinanceInputError(
                    _("Geben Sie ein gültiges Buchungsdatum im Format 2026-07-02 an.")
                ) from exc
            if parsed_date != row["date"] or not start <= parsed_date <= end:
                raise FinanceInputError(
                    _("Das Buchungsdatum liegt ausserhalb des Quartals.")
                )
            if row["category"] not in CATEGORIES[kind] or not re.fullmatch(
                r"[A-Z0-9][A-Z0-9-]{2,39}", row["evidence_code"]
            ):
                raise FinanceInputError(
                    _(
                        "Prüfen Sie die Kategorie für diese Buchungsart und die bereinigte Belegkennung mit 3 bis 40 Grossbuchstaben, Ziffern oder Bindestrichen."
                    )
                )
            if kind == "receipt":
                if row["fund"] == "held":
                    if row["category"] != "held":
                        raise FinanceInputError(
                            _(
                                "Noch nicht angenommene Finanzierung muss im Fonds held mit Kategorie held stehen."
                            )
                        )
                elif not row["funding_check"] or row["category"] == "held":
                    raise FinanceInputError(
                        _(
                            "Die private Referenz zur tatsächlichen Prüfung der Finanzierungsunabhängigkeit fehlt."
                        )
                    )
            if kind in ("transfer", "allocation"):
                destination = (row["to_account_id"], row["to_fund"])
                if destination not in balances or destination == key:
                    raise FinanceInputError(
                        _(
                            "Eine interne Bewegung braucht zwei unterschiedliche erfasste Konto-Fonds-Kombinationen."
                        )
                    )
                if kind == "transfer" and (
                    destination[0] == key[0] or destination[1] != key[1]
                ):
                    raise FinanceInputError(
                        _("Ein Transfer wechselt das Konto und behält denselben Fonds.")
                    )
                if kind == "allocation" and (
                    destination[0] != key[0] or destination[1] == key[1]
                ):
                    raise FinanceInputError(
                        _(
                            "Eine Fondsumbuchung wechselt den Fonds innerhalb desselben Kontos."
                        )
                    )
                if (
                    kind == "allocation"
                    and key[1] == "held"
                    and destination[1] != "held"
                    and not row["funding_check"]
                ):
                    raise FinanceInputError(
                        _(
                            "Die Freigabe zurückgehaltener Mittel braucht die tatsächliche Unabhängigkeitsprüfung in funding_check."
                        )
                    )
                changes[destination] += amount
            elif row["to_account_id"] or row["to_fund"]:
                raise FinanceInputError(
                    _(
                        "Lassen Sie bei externen Buchungen to_account_id und to_fund leer."
                    )
                )
            changes[key] += amount if kind == "receipt" else -amount
            if KINDS[kind]:
                totals[KINDS[kind]] += amount
            public_bookings.append(
                {
                    "date": row["date"],
                    "kind": kind,
                    "amount_cents": amount,
                    "currency": "CHF",
                    "category": row["category"],
                    "purpose": {
                        locale: text(row["purpose_" + locale], limit=1000)
                        for locale in LOCALES
                    },
                    "evidence_code": row["evidence_code"],
                }
            )
    for key, (opening, closing, row_number) in balances.items():
        if opening + changes[key] != closing:
            raise FinanceInputError(
                _(
                    "Anfangsbestand plus Buchungen stimmt für dieses Konto und diesen Fonds nicht mit dem Schlussbestand überein."
                ),
                field="balances",
                row=row_number,
            )
    seen_commitments = set()
    for row_number, row in enumerate(private["commitments"], start=2):
        with _location("commitments", row_number):
            if (
                not row["source_id"]
                or row["source_id"] in seen_commitments
                or row["source_id"] in sources
                or row["fund"] not in FUNDS
                or not row["private_evidence"]
            ):
                raise FinanceInputError(
                    _(
                        "Prüfen Sie Quellkennung, Fonds und Quellenreferenz der Verpflichtung. Ein bereits gebuchter oder doppelt erfasster Beleg darf nicht als unbezahlte Verpflichtung erscheinen."
                    )
                )
            seen_commitments.add(row["source_id"])
            amount = _cents(row["amount_chf"], positive=True)
            totals["commitments_cents"] += amount
            funds[row["fund"]]["commitments_cents"] += amount
    totals["spendable_bank_cents"] = (
        funds["general"]["bank_cents"] - funds["general"]["commitments_cents"]
    )
    public = {
        "quarter": quarter,
        "start": start,
        "end": end,
        "currency": "CHF",
        "totals": totals,
        "funds": funds,
        "bookings": public_bookings,
    }
    private["quarter"] = quarter
    return private, public


def validate_continuity(private, quarters):
    """Compare complete report inputs without database access."""
    quarter = private["quarter"]
    ordinal = int(quarter[:4]) * 4 + int(quarter[-1]) - 1
    previous = f"{(ordinal - 1) // 4}-Q{(ordinal - 1) % 4 + 1}"
    following = f"{(ordinal + 1) // 4}-Q{(ordinal + 1) % 4 + 1}"
    if quarters and quarter not in quarters and previous not in quarters:
        raise FinanceInputError(
            _(
                "Veröffentlichen Sie aufeinanderfolgende Quartale ab dem festgelegten Anfangszeitraum."
            ),
            field="quarter",
        )

    def amounts(report, column):
        return {
            (row["account_id"], row["kind"], row["fund"]): row[column]
            for row in report["balances"]
        }

    def matches(left, right):
        return all(
            Decimal(left.get(key, "0")) == Decimal(right.get(key, "0"))
            for key in left.keys() | right.keys()
        )

    if previous in quarters and not matches(
        amounts(quarters[previous], "closing_chf"), amounts(private, "opening_chf")
    ):
        raise FinanceInputError(
            _(
                "Die Anfangsbestände müssen den zuletzt veröffentlichten Schlussbeständen des Vorquartals entsprechen."
            ),
            field="balances",
        )
    if following in quarters and not matches(
        amounts(private, "closing_chf"), amounts(quarters[following], "opening_chf")
    ):
        raise FinanceInputError(
            _(
                "Diese Korrektur würde die Anfangsbestände des folgenden veröffentlichten Quartals ungültig machen."
            ),
            field="balances",
        )
