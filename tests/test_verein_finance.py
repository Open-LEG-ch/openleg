# SPDX-License-Identifier: AGPL-3.0-or-later
"""Quarterly all-funds reports through HTTP and native isolated PostgreSQL."""

import csv
import io
import re
from html import unescape

import pytest

from tests import test_verein_membership

app_module = test_verein_membership.app_module
isolated_environment = test_verein_membership.isolated_environment
web = test_verein_membership.web
membership = test_verein_membership.membership
pytestmark = pytest.mark.integration
PATH = "/verein/vorstand/finanzen"


def _csv(columns, rows):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(columns.split(","))
    writer.writerows(rows)
    return output.getvalue()


def _data():
    bookings = [
        [
            "private-donor@example.invalid",
            "2026-07-02",
            "receipt",
            "twint-private",
            "general",
            "",
            "",
            "100.00",
            "donation",
        ],
        [
            "private-fee-1",
            "2026-07-02",
            "fee",
            "twint-private",
            "general",
            "",
            "",
            "2.00",
            "provider_fee",
        ],
        [
            "private-payout-1",
            "2026-07-03",
            "transfer",
            "twint-private",
            "general",
            "bank-private",
            "general",
            "98.00",
            "internal",
        ],
        [
            "private-refund-1",
            "2026-07-04",
            "refund",
            "bank-private",
            "general",
            "",
            "",
            "20.00",
            "refund",
        ],
        [
            "private-grant-1",
            "2026-07-05",
            "receipt",
            "bank-private",
            "restricted",
            "",
            "",
            "200.00",
            "grant",
        ],
        [
            "private-hosting-1",
            "2026-07-06",
            "expense",
            "bank-private",
            "general",
            "",
            "",
            "30.00",
            "hosting",
        ],
        [
            "private-transfer-1",
            "2026-07-07",
            "transfer",
            "bank-private",
            "general",
            "other-private",
            "general",
            "100.00",
            "internal",
        ],
    ]
    for index, row in enumerate(bookings):
        row.extend(
            [
                "Fiktiver Testbeleg",
                "Fictional test evidence",
                "Justificatif fictif",
                "Giustificativo fittizio",
                f"PUB-{index}",
                "private-source-record",
                "private-independence-review" if row[2] == "receipt" else "",
            ]
        )
    return {
        "quarter": "2026-Q3",
        "scope_evidence": "private-board-approved-all-accounts-and-reporting-policy",
        "opening_evidence": "private-opening-statements",
        "all_funds": "yes",
        "balances": _csv(
            "account_id,kind,fund,opening_chf,closing_chf,pending_chf,private_evidence",
            [
                [
                    "bank-private",
                    "bank",
                    "general",
                    "1000.00",
                    "948.00",
                    "0.00",
                    "private-statement",
                ],
                [
                    "bank-private",
                    "bank",
                    "reserve",
                    "3000.00",
                    "3000.00",
                    "0.00",
                    "private-statement",
                ],
                [
                    "bank-private",
                    "bank",
                    "restricted",
                    "0.00",
                    "200.00",
                    "0.00",
                    "private-statement",
                ],
                [
                    "other-private",
                    "bank",
                    "general",
                    "0.00",
                    "100.00",
                    "0.00",
                    "private-statement",
                ],
                [
                    "twint-private",
                    "clearing",
                    "general",
                    "0.00",
                    "0.00",
                    "50.00",
                    "private-provider",
                ],
            ],
        ),
        "bookings": _csv(
            "source_id,date,kind,account_id,fund,to_account_id,to_fund,amount_chf,category,purpose_de_CH,purpose_en,purpose_fr_CH,purpose_it_CH,evidence_code,private_evidence,funding_check",
            bookings,
        ),
        "commitments": _csv(
            "source_id,fund,amount_chf,private_evidence",
            [["private-unpaid-invoice", "general", "120.00", "private-invoice"]],
        ),
        "correction_reason": "",
    }


def _setup(membership):
    client, _mail, store, _actor, _path = test_verein_membership._accepted(membership)
    import database
    from store import verein_finance

    with database.get_connection() as conn, conn.cursor() as cur:
        verein_finance.create_tables(cur)
    assert client.get(PATH).status_code == 200
    return client, store


def _prepare(client, data=None):
    result = client.post(
        PATH,
        data={
            **(data or _data()),
            "csrf_token": test_verein_membership._csrf(client, PATH),
        },
    )
    assert result.status_code == 303, result.text
    return result.headers["Location"]


def _other_reviewer(client, store):
    store.provision_reviewer(
        "second@example.invalid", "Second reviewer", "different-correct-horse-password"
    )
    result = client.post(
        "/verein/vorstand/login",
        data={
            "csrf_token": test_verein_membership._csrf(
                client, "/verein/vorstand/login"
            ),
            "email": "second@example.invalid",
            "password": "different-correct-horse-password",
        },
    )
    assert result.status_code == 303


def _publish(client, path):
    return client.post(
        path + "/freigeben",
        data={
            "csrf_token": test_verein_membership._csrf(client, path),
            "review_evidence": "private-actual-second-person-source-review",
            "sources_checked": "yes",
            "scope_checked": "yes",
            "redaction_checked": "yes",
        },
    )


def test_quarter_reconciles_all_funds_and_publishes_only_after_independent_review(
    membership,
):
    client, store = _setup(membership)
    path = _prepare(client)
    assert "2026-Q3" not in client.get("/verein/finanzen").text
    assert _publish(client, path).status_code == 409
    _other_reviewer(client, store)
    assert _publish(client, path).status_code == 303
    response = client.get("/verein/finanzen/2026-Q3.json")
    assert response.status_code == 200
    report = response.json
    assert report["totals"] == {
        "opening_bank_cents": 400000,
        "closing_bank_cents": 424800,
        "opening_clearing_cents": 0,
        "closing_clearing_cents": 0,
        "gross_receipts_cents": 30000,
        "refunds_cents": 2000,
        "fees_cents": 200,
        "expenses_cents": 3000,
        "pending_cents": 5000,
        "reserve_cents": 300000,
        "restricted_cents": 20000,
        "held_cents": 0,
        "commitments_cents": 12000,
        "spendable_bank_cents": 92800,
    }
    assert len(report["bookings"]) == 7
    assert len({item["id"] for item in report["bookings"]}) == 7
    assert all(
        set(item)
        == {
            "id",
            "date",
            "kind",
            "amount_cents",
            "currency",
            "category",
            "purpose",
            "evidence_code",
        }
        for item in report["bookings"]
    )
    assert "private-" not in response.text
    page = client.get("/verein/finanzen/2026-Q3")
    assert page.status_code == 200
    assert "928.00" in page.text
    assert "50.00" in page.text
    assert "no-store" in client.get(path).headers["Cache-Control"]


def _change(data, name, change):
    rows = list(csv.reader(io.StringIO(data[name])))
    change(rows)
    data[name] = _csv(",".join(rows[0]), rows[1:])
    return data


def test_correction_keeps_prior_snapshot_and_stable_ids(membership):
    client, store = _setup(membership)
    original = _prepare(client)
    assert _prepare(client) == original
    _other_reviewer(client, store)
    assert _publish(client, original).status_code == 303
    prior = client.get("/verein/finanzen/2026-Q3.json").json
    changed = _data()
    changed["correction_reason"] = "Public purpose clarified"
    _change(
        changed,
        "bookings",
        lambda rows: rows[1].__setitem__(9, "Korrigierter Testzweck"),
    )
    correction = _prepare(client, changed)
    assert _publish(client, correction).status_code == 409
    client.post(
        "/verein/vorstand/login",
        data={
            "csrf_token": test_verein_membership._csrf(
                client, "/verein/vorstand/login"
            ),
            "email": "reviewer@example.org",
            "password": "correct-horse-battery-staple",
        },
    )
    assert _publish(client, correction).status_code == 303
    latest = client.get("/verein/finanzen/2026-Q3.json").json
    assert latest["version"] == 2
    assert latest["correction_reason"] == "Public purpose clarified"
    assert [item["id"] for item in latest["bookings"]] == [
        item["id"] for item in prior["bookings"]
    ]
    assert client.get("/verein/finanzen/2026-Q3.json?version=1").json == prior
    assert latest["bookings"][0]["purpose"]["de_CH"] == "Korrigierter Testzweck"
    assert "version=1" in client.get("/verein/finanzen/2026-Q3").text
    removed = _change(_data(), "bookings", lambda rows: rows.pop())
    removed["correction_reason"] = "Try to remove a published source"
    _change(
        removed,
        "balances",
        lambda rows: (
            rows[1].__setitem__(4, "1048.00"),
            rows[4].__setitem__(4, "0.00"),
        ),
    )
    assert (
        client.post(
            PATH,
            data={**removed, "csrf_token": test_verein_membership._csrf(client, PATH)},
        ).status_code
        == 409
    )


@pytest.mark.parametrize(
    "fault",
    [
        "mismatch",
        "duplicate",
        "net_payout_as_income",
        "unreviewed_funding",
        "unconfirmed_in_bank",
        "negative",
        "precision",
        "future",
        "missing_scope",
        "missing_account",
        "missing_locale",
    ],
)
def test_unreconciled_or_incomplete_report_stays_private_and_retains_input(
    membership, fault
):
    client, _store = _setup(membership)
    data = _data()
    if fault == "mismatch":
        _change(data, "balances", lambda rows: rows[1].__setitem__(4, "949.00"))
    elif fault == "duplicate":
        _change(data, "bookings", lambda rows: rows.append(rows[1]))
    elif fault == "net_payout_as_income":

        def payout(rows):
            rows[3][2], rows[3][8], rows[3][15] = (
                "receipt",
                "donation",
                "private-review",
            )

        _change(data, "bookings", payout)
    elif fault == "unreviewed_funding":
        _change(data, "bookings", lambda rows: rows[1].__setitem__(15, ""))
    elif fault == "unconfirmed_in_bank":
        _change(data, "balances", lambda rows: rows[1].__setitem__(5, "50.00"))
    elif fault == "negative":
        _change(data, "bookings", lambda rows: rows[1].__setitem__(7, "-100.00"))
    elif fault == "precision":
        _change(data, "bookings", lambda rows: rows[1].__setitem__(7, "100.001"))
    elif fault == "future":
        data["quarter"] = "2099-Q3"
    elif fault == "missing_scope":
        data["all_funds"] = ""
    elif fault == "missing_account":
        _change(data, "balances", lambda rows: rows.pop())
    else:
        _change(data, "bookings", lambda rows: rows[1].__setitem__(12, ""))
    response = client.post(
        PATH, data={**data, "csrf_token": test_verein_membership._csrf(client, PATH)}
    )
    assert response.status_code == 409
    assert 'role="alert"' in response.text
    assert (
        unescape(
            re.search(
                r'<textarea[^>]*name="bookings"[^>]*>(.*?)</textarea>',
                response.text,
                re.DOTALL,
            )[1]
        )
        == data["bookings"]
    )
    assert client.get("/verein/finanzen/2026-Q3.json").status_code == 404


def test_sources_cannot_be_imported_into_two_quarters_and_openings_must_follow(
    membership,
):
    client, store = _setup(membership)
    first = _data()
    first["quarter"] = "2026-Q2"
    _change(
        first,
        "bookings",
        lambda rows: [
            row.__setitem__(1, row[1].replace("2026-07", "2026-04")) for row in rows[1:]
        ],
    )
    original = _prepare(client, first)
    _other_reviewer(client, store)
    assert _publish(client, original).status_code == 303
    following = _data()

    def balances(rows):
        rows[1][3:5] = ["948.00", "896.00"]
        rows[3][3:5] = ["200.00", "400.00"]
        rows[4][3:5] = ["100.00", "200.00"]

    _change(following, "balances", balances)
    rejected = client.post(
        PATH,
        data={**following, "csrf_token": test_verein_membership._csrf(client, PATH)},
    )
    assert rejected.status_code == 409
    _change(
        following,
        "bookings",
        lambda rows: [row.__setitem__(0, "new-" + row[0]) for row in rows[1:]],
    )
    second = _prepare(client, following)
    assert second != original
    inconsistent = _data()
    _change(
        inconsistent,
        "bookings",
        lambda rows: [row.__setitem__(0, "other-" + row[0]) for row in rows[1:]],
    )
    inconsistent["correction_reason"] = "Opening mismatch"
    assert (
        client.post(
            PATH,
            data={
                **inconsistent,
                "csrf_token": test_verein_membership._csrf(client, PATH),
            },
        ).status_code
        == 409
    )


def test_revocation_csrf_and_site_gate_protect_report_actions(membership, web):
    client, store = _setup(membership)
    path = _prepare(client)
    assert (
        client.post(path + "/freigeben", data={"review_evidence": "forged"}).status_code
        == 403
    )
    _other_reviewer(client, store)
    with client.session_transaction() as session:
        actor = store.reviewer(session["verein_session"])
    store.revoke_reviewer(str(actor["id"]))
    assert client.get(path).status_code == 401
    assert web.test_client().get(PATH).status_code == 401
    assert web.test_client().get("/verein/finanzen/2026-Q3.json").status_code == 404
    web.config["SITE_RELEASE_ENABLED"] = False
    assert client.get(PATH).status_code == 404
    assert client.get("/verein/finanzen").status_code == 404


def test_report_unavailable_is_distinct_from_no_published_reports(
    membership, monkeypatch
):
    client, _store = _setup(membership)
    empty = client.get("/verein/finanzen")
    assert empty.status_code == 200
    assert "kein Kontostand" in empty.text
    from contextlib import contextmanager

    import psycopg2

    import database

    @contextmanager
    def unavailable():
        raise psycopg2.OperationalError("private-source-outage")
        yield

    monkeypatch.setattr(database, "get_connection", unavailable)
    failed = client.get("/verein/finanzen")
    assert failed.status_code == 503
    assert "private-source-outage" not in failed.text


def test_correction_form_copies_private_source_without_editing_immutable_snapshot(
    membership,
):
    client, _store = _setup(membership)
    path = _prepare(client)
    copy = client.get(PATH + "?copy=" + path.rsplit("/", 1)[1])
    assert copy.status_code == 200
    values = re.search(
        r'<textarea[^>]*name="bookings"[^>]*>(.*?)</textarea>', copy.text, re.DOTALL
    )[1]
    assert "private-donor@example.invalid" in unescape(values)
    assert "2026-Q3" in copy.text
    assert client.get(PATH + "?copy=missing").status_code == 404
