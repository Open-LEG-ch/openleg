# SPDX-License-Identifier: AGPL-3.0-or-later
"""Verified association recipients through HTTP and native PostgreSQL."""

import pytest

from tests import test_verein_finance, test_verein_membership

app_module = test_verein_membership.app_module
isolated_environment = test_verein_membership.isolated_environment
web = test_verein_membership.web
membership = test_verein_membership.membership
pytestmark = pytest.mark.integration
PATH = "/verein/vorstand/zahlungsempfaenger"
PUBLIC = "/verein/spenden"


def _data():
    return {
        "name": "Fictional QR test association",
        "iban": "CH58 0079 1123 0008 8901 2",
        "street": "Rue du Lac",
        "building_number": "1268",
        "postal_code": "2501",
        "city": "Biel/Bienne",
        "country": "CH",
        "message": "Fictional donation test only",
        "account_evidence": "private-fictional-account-ownership-proof",
        "address_evidence": "private-fictional-approved-public-address",
        "qr_evidence": "private-fictional-bank-QR-validation",
    }


def _setup(membership):
    client, _mail, store, _actor, _path = test_verein_membership._accepted(membership)
    import database
    from store import verein_payments

    with database.get_connection() as conn, conn.cursor() as cur:
        verein_payments.create_tables(cur)
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


def _approve(client, path):
    return client.post(
        path + "/freigeben",
        data={
            "csrf_token": test_verein_membership._csrf(client, path),
            "ownership_checked": "yes",
            "address_checked": "yes",
            "qr_checked": "yes",
            "evidence": "private-fictional-independent-recipient-review",
            "qr_evidence": "private-fictional-actual-candidate-qr-review",
        },
    )


def test_only_an_independently_checked_recipient_can_supply_public_qr_instructions(
    membership,
):
    client, store = _setup(membership)
    assert "CH58" not in client.get(PUBLIC).text
    path = _prepare(client)
    assert "CH58" not in client.get(PUBLIC).text
    assert _approve(client, path).status_code == 409
    test_verein_finance._other_reviewer(client, store)
    assert _approve(client, path).status_code == 303
    page = client.get(PUBLIC + "?amount=25.00&status=success")
    assert page.status_code == 200
    assert "Fictional QR test association" in page.text
    assert "CH58 0079 1123 0008 8901 2" in page.text
    assert "private-fictional" not in page.text
    identifier = path.rsplit("/", 1)[1]
    code = client.get(PUBLIC + "/qr/" + identifier + ".svg?amount=25.00")
    assert code.status_code == 200
    assert code.mimetype == "image/svg+xml"
    assert "<svg" in code.text
    pdf = client.get(PUBLIC + "/qr/" + identifier + ".pdf?amount=25.00")
    assert pdf.status_code == 200
    assert pdf.mimetype == "application/pdf" and pdf.data.startswith(b"%PDF-")
    assert "no-store" in client.get(path).headers["Cache-Control"]


def test_second_reviewer_can_validate_a_private_qr_draft_before_activation(membership):
    client, store = _setup(membership)
    data = _data()
    data["qr_evidence"] = ""
    path = _prepare(client, data)
    private = client.get(path + "/vorschau.svg")
    assert private.status_code == 200
    assert "no-store" in private.headers["Cache-Control"]
    assert (
        client.get(PUBLIC + "/qr/" + path.rsplit("/", 1)[1] + ".svg").status_code == 404
    )
    test_verein_finance._other_reviewer(client, store)
    assert _approve(client, path).status_code == 303


def test_withdrawal_removes_collection_without_falling_back_to_an_old_account(
    membership,
):
    client, store = _setup(membership)
    first = _prepare(client)
    test_verein_finance._other_reviewer(client, store)
    assert _approve(client, first).status_code == 303
    assert client.get(PUBLIC).headers.get("Cache-Control") == "no-store"
    replacement_data = _data()
    replacement_data["message"] = "Fictional replacement profile"
    replacement = _prepare(client, replacement_data)
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
    assert _approve(client, replacement).status_code == 303
    first_id = first.rsplit("/", 1)[1]
    second_id = replacement.rsplit("/", 1)[1]
    assert client.get(PUBLIC + "/qr/" + first_id + ".svg").status_code == 410
    stale = client.post(
        first + "/zurueckziehen",
        data={
            "csrf_token": test_verein_membership._csrf(client, first),
            "reason": "Stale request",
        },
    )
    assert stale.status_code == 409
    assert client.get(PUBLIC + "/qr/" + second_id + ".svg").status_code == 200
    removed = client.post(
        replacement + "/zurueckziehen",
        data={
            "csrf_token": test_verein_membership._csrf(client, replacement),
            "reason": "Fictional safety check",
        },
    )
    assert removed.status_code == 303
    assert "CH58" not in client.get(PUBLIC).text
    assert client.get(PUBLIC + "/qr/" + first_id + ".svg").status_code == 404
    assert client.get(PUBLIC + "/qr/" + second_id + ".pdf").status_code == 404


@pytest.mark.parametrize(
    "field,value",
    [
        ("iban", "CH59 0079 1123 0008 8901 2"),
        ("iban", "CH44 3199 9123 0008 8901 2"),
        ("name", "Injected\nSPC"),
        ("name", "Unsupported 😀"),
        ("name", "x" * 71),
        ("country", "ZZ"),
        ("city", ""),
        ("street", ""),
        ("account_evidence", ""),
    ],
)
def test_invalid_recipient_is_located_and_never_supplies_public_details(
    membership, field, value
):
    client, _store = _setup(membership)
    data = {**_data(), field: value}
    result = client.post(
        PATH, data={**data, "csrf_token": test_verein_membership._csrf(client, PATH)}
    )
    assert result.status_code == 409
    assert f'href="#{field}"' in result.text
    assert f'id="{field}-error"' in result.text
    assert 'aria-invalid="true"' in result.text
    assert "CH58" not in client.get(PUBLIC).text


@pytest.mark.parametrize("value", ["0", "-1", "1.001", "NaN", "1,50", "1000000000.00"])
def test_invalid_donation_amount_cannot_generate_a_different_qr(membership, value):
    client, store = _setup(membership)
    path = _prepare(client)
    test_verein_finance._other_reviewer(client, store)
    assert _approve(client, path).status_code == 303
    response = client.get(PUBLIC, query_string={"amount": value})
    assert response.status_code == 400
    assert 'id="amount-error"' in response.text
    assert "Fictional QR test association" in response.text
    assert (
        client.get(
            PUBLIC + "/qr/" + path.rsplit("/", 1)[1] + ".svg",
            query_string={"amount": value},
        ).status_code
        == 400
    )


def test_svg_contains_the_specified_non_reference_chf_payload_with_blank_payer(
    membership,
):
    import xml.etree.ElementTree as ET

    client, store = _setup(membership)
    path = _prepare(client)
    test_verein_finance._other_reviewer(client, store)
    assert _approve(client, path).status_code == 303
    url = PUBLIC + "/qr/" + path.rsplit("/", 1)[1] + ".svg"
    result = client.get(url + "?amount=25.00")
    root = ET.fromstring(result.data)
    data = root.find("{http://www.w3.org/2000/svg}metadata").text.split("\n")
    assert data == [
        "SPC",
        "0200",
        "1",
        "CH5800791123000889012",
        "S",
        "Fictional QR test association",
        "Rue du Lac",
        "1268",
        "2501",
        "Biel/Bienne",
        "CH",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "25.00",
        "CHF",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "NON",
        "",
        "Fictional donation test only",
        "EPD",
    ]
    blank = (
        ET.fromstring(client.get(url).data)
        .find("{http://www.w3.org/2000/svg}metadata")
        .text.split("\n")
    )
    assert blank[18] == "" and len(blank) == 31
    assert root.attrib["width"] == "56mm"
    assert root.find("{http://www.w3.org/2000/svg}svg").attrib["width"] == "7"
    assert not root.findall(".//{http://www.w3.org/2000/svg}style")


@pytest.mark.parametrize(
    "setting",
    [
        "VEREIN_FORMATION_DATE",
        "VEREIN_DATA_CONTROLLER",
        "VEREIN_PRIVACY_VERSION",
        "VEREIN_CONTACT_ADDRESS",
    ],
)
def test_verified_bank_details_wait_for_actual_formation_and_privacy_setup(
    membership, web, setting
):
    client, store = _setup(membership)
    path = _prepare(client)
    test_verein_finance._other_reviewer(client, store)
    assert _approve(client, path).status_code == 303
    web.config[setting] = ""
    assert "CH58" not in client.get(PUBLIC).text
    assert (
        client.get(PUBLIC + "/qr/" + path.rsplit("/", 1)[1] + ".svg").status_code == 404
    )
    web.config["SITE_RELEASE_ENABLED"] = False
    assert client.get(PUBLIC).status_code == 404
    assert client.get(path).status_code == 404


def test_private_recipient_actions_require_current_role_and_csrf(membership, web):
    client, store = _setup(membership)
    path = _prepare(client)
    assert web.test_client().get(path + "/vorschau.svg").status_code == 401
    assert (
        client.post(path + "/freigeben", data={"evidence": "forged"}).status_code == 403
    )
    test_verein_finance._other_reviewer(client, store)
    with client.session_transaction() as session:
        actor = store.reviewer(session["verein_session"])
    store.revoke_reviewer(str(actor["id"]))
    assert client.get(path + "/vorschau.pdf").status_code == 401
    assert "CH58" not in client.get(PUBLIC).text
