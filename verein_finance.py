# SPDX-License-Identifier: AGPL-3.0-or-later
"""CHF report reconciliation. Source records and policy adoption stay private."""

import calendar
import csv
import io
import re
from collections import defaultdict
from datetime import date, datetime
from zoneinfo import ZoneInfo

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


def text(value, *, required=True, limit=500):
    if not isinstance(value, str) or len(value) > limit or "\x00" in value:
        raise FinanceInputError("Invalid text")
    value = value.strip()
    if required and not value:
        raise FinanceInputError("Required text missing")
    return value


def _rows(value, name):
    value = text(value, limit=800000)
    try:
        reader = csv.DictReader(io.StringIO(value), strict=True)
        if reader.fieldnames != COLUMNS[name].split(","):
            raise FinanceInputError("Use the provided CSV column order")
        rows = []
        for row in reader:
            if len(rows) >= 3000 or None in row or any(v is None for v in row.values()):
                raise FinanceInputError("Invalid or oversized CSV")
            rows.append(
                {key: text(val, required=False, limit=1000) for key, val in row.items()}
            )
        return rows
    except csv.Error as exc:
        raise FinanceInputError("Invalid CSV") from exc


def _cents(value, *, positive=False):
    if not re.fullmatch(r"(?:0|[1-9][0-9]{0,8})(?:\.[0-9]{1,2})?", value):
        raise FinanceInputError("Use nonnegative CHF with at most two decimal places")
    whole, _, fraction = value.partition(".")
    amount = int(whole) * 100 + int(fraction.ljust(2, "0"))
    if positive and not amount:
        raise FinanceInputError("A booking must be positive")
    return amount


def reconcile(data):
    quarter = text(data.get("quarter", ""))
    if not re.fullmatch(r"20[0-9]{2}-Q[1-4]", quarter):
        raise FinanceInputError("Select a calendar quarter")
    year, number = int(quarter[:4]), int(quarter[-1])
    month = number * 3
    start = f"{year}-{month - 2:02d}-01"
    end = f"{year}-{month:02d}-{calendar.monthrange(year, month)[1]}"
    if end >= datetime.now(ZoneInfo("Europe/Zurich")).date().isoformat():
        raise FinanceInputError("Only completed calendar quarters can be reported")
    private = {
        name: text(data.get(name, ""))
        for name in ("scope_evidence", "opening_evidence")
    }
    if data.get("all_funds") != "yes":
        raise FinanceInputError(
            "Confirm complete account/provider and currency coverage"
        )
    private["balances"] = _rows(data.get("balances", ""), "balances")
    private["bookings"] = _rows(data.get("bookings", ""), "bookings")
    private["commitments"] = _rows(data.get("commitments", ""), "commitments")
    if not private["balances"]:
        raise FinanceInputError("All controlled accounts must be included")
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
    for row in private["balances"]:
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
                "Invalid account/fund balance or missing source evidence"
            )
        accounts[account] = kind
        opening, closing, pending = (
            _cents(row[name]) for name in ("opening_chf", "closing_chf", "pending_chf")
        )
        if pending and kind != "clearing":
            raise FinanceInputError(
                "Pending provider payments are separate from bank cash"
            )
        balances[key] = (opening, closing)
        totals[f"opening_{kind}_cents"] += opening
        totals[f"closing_{kind}_cents"] += closing
        totals["pending_cents"] += pending
        funds[fund][f"{kind}_cents"] += closing
        funds[fund]["pending_cents"] += pending
        if fund != "general":
            totals[f"{fund}_cents"] += closing
    public_bookings, sources = [], set()
    for row in private["bookings"]:
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
            raise FinanceInputError("Invalid or duplicate booking/source")
        sources.add(source)
        try:
            parsed_date = date.fromisoformat(row["date"]).isoformat()
        except ValueError as exc:
            raise FinanceInputError("Invalid booking date") from exc
        if parsed_date != row["date"] or not start <= parsed_date <= end:
            raise FinanceInputError("Booking is outside the quarter")
        if row["category"] not in CATEGORIES[kind] or not re.fullmatch(
            r"[A-Z0-9][A-Z0-9-]{2,39}", row["evidence_code"]
        ):
            raise FinanceInputError(
                "Choose the correct category and a redacted evidence code"
            )
        if kind == "receipt":
            if row["fund"] == "held":
                if row["category"] != "held":
                    raise FinanceInputError("Unaccepted funding must be labelled held")
            elif not row["funding_check"] or row["category"] == "held":
                raise FinanceInputError("Actual funding independence review required")
        if kind in ("transfer", "allocation"):
            destination = (row["to_account_id"], row["to_fund"])
            if destination not in balances or destination == key:
                raise FinanceInputError(
                    "Internal movement needs two distinct recorded balances"
                )
            if kind == "transfer" and (
                destination[0] == key[0] or destination[1] != key[1]
            ):
                raise FinanceInputError(
                    "Transfers change accounts, not fund designation"
                )
            if kind == "allocation" and (
                destination[0] != key[0] or destination[1] == key[1]
            ):
                raise FinanceInputError("Allocations change funds within an account")
            if (
                kind == "allocation"
                and key[1] == "held"
                and destination[1] != "held"
                and not row["funding_check"]
            ):
                raise FinanceInputError(
                    "Held funding requires actual independence review before release"
                )
            changes[destination] += amount
        elif row["to_account_id"] or row["to_fund"]:
            raise FinanceInputError("External bookings have no internal destination")
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
    for key, (opening, closing) in balances.items():
        if opening + changes[key] != closing:
            raise FinanceInputError(
                "Account/fund does not reconcile to its closing statement"
            )
    seen_commitments = set()
    for row in private["commitments"]:
        if (
            not row["source_id"]
            or row["source_id"] in seen_commitments
            or row["fund"] not in FUNDS
            or not row["private_evidence"]
        ):
            raise FinanceInputError("Invalid or duplicate unpaid commitment")
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
