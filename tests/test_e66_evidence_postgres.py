# SPDX-License-Identifier: AGPL-3.0-or-later
"""Import external E66 identities through the authenticated evidence boundary."""

import hashlib
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from threading import Event
from zoneinfo import ZoneInfo

import pytest

import billing_runner
import vnb_calculated_values
from tests import test_calculated_values_lifecycle_postgres as lifecycle

interest_database = lifecycle.interest_database
calculated_client = lifecycle.calculated_client
pytestmark = pytest.mark.integration


@pytest.fixture
def assignment_request(calculated_client):
    client, db, payload = calculated_client
    point = "SYNTHETIC-METER-1"
    assert (
        db.upsert_metering_points(
            [{"metering_point_id": point, "vnb_community_id": "SYNTHETIC-EXTERNAL-LEG"}]
        )
        == 1
    )
    return (
        client,
        db,
        payload,
        point,
        {
            "effective_from": "2026-09-01T00:00:00+02:00",
            "effective_until": "2026-11-01T00:00:00+01:00",
            "source_reference": "SYNTHETIC-MEMBERSHIP-STATEMENT",
            "confirmed_membership": True,
        },
    )


def test_operator_can_record_effective_validity_for_an_existing_meter_assignment(
    calculated_client,
):
    client, db, payload = calculated_client
    point = "SYNTHETIC-METER-1"
    assert (
        db.upsert_metering_points(
            [{"metering_point_id": point, "vnb_community_id": "SYNTHETIC-EXTERNAL-LEG"}]
        )
        == 1
    )

    response = client.post(
        f"/admin/vnb-calculated-values/dietikon/mappings/{point}",
        headers={"X-Admin-Token": "synthetic-admin"},
        json={
            "effective_from": "2026-09-01T00:00:00+02:00",
            "effective_until": "2026-11-01T00:00:00+01:00",
            "source_reference": "SYNTHETIC-MEMBERSHIP-STATEMENT",
            "confirmed_membership": True,
        },
    )

    assert response.status_code == 201
    assert response.headers["Cache-Control"] == "no-store"
    stored = db.get_metering_point(point)
    assert stored["community_id"] == payload["community_id"]
    assert stored["building_id"] == "member"
    assert (
        stored["assignment_effective_from"].isoformat() == "2026-08-31T22:00:00+00:00"
    )
    assert (
        stored["assignment_effective_until"].isoformat() == "2026-10-31T23:00:00+00:00"
    )
    assert stored["assignment_reference"] == "SYNTHETIC-MEMBERSHIP-STATEMENT"
    assert stored["assignment_actor"] == "admin-token"
    assert stored["assignment_recorded_at"] is not None


@pytest.mark.parametrize(
    "token,territory,expected",
    [
        (None, "dietikon", 403),
        ("wrong", "dietikon", 403),
        ("synthetic-admin", "baden", 404),
    ],
)
def test_assignment_statement_is_private_and_tenant_scoped(
    assignment_request, token, territory, expected
):
    client, db, _, point, payload = assignment_request
    response = client.post(
        f"/admin/vnb-calculated-values/{territory}/mappings/{point}",
        headers={"X-Admin-Token": token} if token else {},
        json=payload,
    )
    assert response.status_code == expected
    assert response.headers["Cache-Control"] == "no-store"
    assert db.get_metering_point(point)["assignment_reference"] is None


@pytest.mark.parametrize(
    "changes",
    [
        {"confirmed_membership": False},
        {"confirmed_membership": "true"},
        {"source_reference": ""},
        {"source_reference": "x" * 129},
        {"effective_from": "2026-09-01"},
        {"effective_from": None},
        {"effective_until": "2026-08-01T00:00:00Z"},
        {"effective_until": "not-a-date"},
    ],
)
def test_invalid_assignment_statement_is_not_recorded(assignment_request, changes):
    client, db, _, point, payload = assignment_request
    response = client.post(
        f"/admin/vnb-calculated-values/dietikon/mappings/{point}",
        headers={"X-Admin-Token": "synthetic-admin"},
        json={**payload, **changes},
    )
    assert response.status_code == 400
    assert db.get_metering_point(point)["assignment_reference"] is None


@pytest.mark.parametrize(
    "incomplete", ["external_community", "directions", "inactive", "unconfirmed"]
)
def test_incomplete_registry_binding_cannot_gain_a_validity_statement(
    assignment_request, incomplete
):
    client, db, _, point, payload = assignment_request
    with db.get_connection() as conn, conn.cursor() as cur:
        if incomplete == "external_community":
            cur.execute("UPDATE metering_points SET vnb_community_id = NULL")
        elif incomplete == "directions":
            cur.execute("UPDATE metering_points SET expected_directions = NULL")
        elif incomplete == "inactive":
            cur.execute("UPDATE metering_points SET active = FALSE")
        else:
            cur.execute("UPDATE community_members SET status = 'invited'")
    response = client.post(
        f"/admin/vnb-calculated-values/dietikon/mappings/{point}",
        headers={"X-Admin-Token": "synthetic-admin"},
        json=payload,
    )
    assert response.status_code == 404
    assert db.get_metering_point(point)["assignment_reference"] is None


@pytest.mark.parametrize(
    "field,value,invalidates",
    [
        ("vnb_community_id", "SYNTHETIC-OTHER-LEG", True),
        ("building_id", "other-member", True),
        ("expected_directions", ["production"], True),
        ("alias", "Synthetic renamed meter", False),
    ],
)
def test_binding_changes_invalidate_the_old_statement(
    assignment_request, field, value, invalidates
):
    client, db, community, point, payload = assignment_request
    assert db.save_building(
        "other-member",
        "other@example.invalid",
        {"address": "Synthetic other", "lat": 47.2, "lon": 8.2},
        {},
        verified=True,
    )
    assert db.insert_invited_member(community["community_id"], "other-member", "member")
    assert db.confirm_invited_member(community["community_id"], "other-member")
    assert (
        client.post(
            f"/admin/vnb-calculated-values/dietikon/mappings/{point}",
            headers={"X-Admin-Token": "synthetic-admin"},
            json=payload,
        ).status_code
        == 201
    )
    assert db.upsert_metering_points([{"metering_point_id": point, field: value}]) == 1
    stored = db.get_metering_point(point)
    for key in (
        "assignment_effective_from",
        "assignment_effective_until",
        "assignment_reference",
        "assignment_actor",
        "assignment_recorded_at",
    ):
        if invalidates:
            assert stored[key] is None
        else:
            assert stored[key] is not None


@pytest.mark.parametrize(
    "change", ["delete_building", "delete_community", "direct_update"]
)
def test_all_binding_writers_clear_the_assignment_statement(assignment_request, change):
    client, db, community, point, payload = assignment_request
    assert db.save_building(
        "other-member",
        "other@example.invalid",
        {"address": "Synthetic other", "lat": 47.2, "lon": 8.2},
        {},
        verified=True,
    )
    assert db.insert_invited_member(community["community_id"], "other-member", "member")
    assert db.confirm_invited_member(community["community_id"], "other-member")
    assert (
        db.upsert_metering_points(
            [{"metering_point_id": point, "building_id": "other-member"}]
        )
        == 1
    )
    assert (
        client.post(
            f"/admin/vnb-calculated-values/dietikon/mappings/{point}",
            headers={"X-Admin-Token": "synthetic-admin"},
            json=payload,
        ).status_code
        == 201
    )
    with db.get_connection() as connection, connection.cursor() as cursor:
        if change == "delete_building":
            cursor.execute(
                "DELETE FROM buildings WHERE building_id = %s", ("other-member",)
            )
        elif change == "delete_community":
            cursor.execute(
                "DELETE FROM communities WHERE community_id = %s",
                (community["community_id"],),
            )
        else:
            cursor.execute(
                "UPDATE metering_points SET building_id = NULL WHERE metering_point_id = %s",
                (point,),
            )
    stored = db.get_metering_point(point)
    for field in (
        "assignment_effective_from",
        "assignment_effective_until",
        "assignment_reference",
        "assignment_actor",
        "assignment_recorded_at",
    ):
        assert stored[field] is None


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("binding", ["external", "directions"])
def test_assignment_recorder_cannot_reattach_proof_after_concurrent_rebinding(
    assignment_request, monkeypatch, existing, binding
):
    from store import calculated_values

    client, db, _, point, payload = assignment_request
    endpoint = f"/admin/vnb-calculated-values/dietikon/mappings/{point}"
    headers = {"X-Admin-Token": "synthetic-admin"}
    if existing:
        assert client.post(endpoint, headers=headers, json=payload).status_code == 201
    validated, resume = Event(), Event()
    original_connection = calculated_values._get_connection

    class PausedCursor:
        def __init__(self, cursor):
            self.cursor = cursor
            self.pause = False

        def execute(self, query, parameters=None):
            self.pause = "FOR SHARE OF b, cm" in query
            return self.cursor.execute(query, parameters)

        def fetchone(self):
            result = self.cursor.fetchone()
            if self.pause and result is not None:
                self.pause = False
                validated.set()
                if not resume.wait(timeout=10):
                    raise TimeoutError("test did not resume validated assignment")
            return result

    class PausedConnection:
        def __init__(self, connection):
            self.connection = connection

        @contextmanager
        def cursor(self):
            with self.connection.cursor() as cursor:
                yield PausedCursor(cursor)

    @contextmanager
    def pause_after_validation():
        with original_connection() as connection:
            yield PausedConnection(connection)

    monkeypatch.setattr(calculated_values, "_get_connection", pause_after_validation)
    with lifecycle.test_interest_postgres.transaction_race(monkeypatch) as race:
        recording = race.start(
            lambda: client.application.test_client().post(
                endpoint, headers=headers, json=payload
            )
        )
        try:
            assert validated.wait(timeout=5)
            with db.get_connection() as connection, connection.cursor() as cursor:
                if binding == "external":
                    cursor.execute(
                        "UPDATE metering_points SET vnb_community_id = %s WHERE metering_point_id = %s",
                        ("SYNTHETIC-REBOUND", point),
                    )
                else:
                    cursor.execute(
                        "UPDATE metering_points SET expected_directions = %s WHERE metering_point_id = %s",
                        (["production"], point),
                    )
        finally:
            resume.set()
        response = race.finish(recording)
    assert response.status_code == 404
    stored = db.get_metering_point(point)
    assert stored["assignment_effective_from"] is None
    assert stored["assignment_reference"] is None
    assert stored["assignment_recorded_at"] is None


def test_assignment_retry_keeps_the_original_statement_time(assignment_request):
    client, db, _, point, payload = assignment_request
    endpoint = f"/admin/vnb-calculated-values/dietikon/mappings/{point}"
    headers = {"X-Admin-Token": "synthetic-admin"}
    assert client.post(endpoint, headers=headers, json=payload).status_code == 201
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE metering_points SET assignment_recorded_at = '2000-01-01T00:00:00Z'"
        )
    original = db.get_metering_point(point)["assignment_recorded_at"]
    assert client.post(endpoint, headers=headers, json=payload).status_code == 201
    assert db.get_metering_point(point)["assignment_recorded_at"] == original


def synthetic_e66(*, status="9", local="0.250", residual="0.750"):
    """Entirely invented source data; no pilot installation is represented."""
    blocks = "".join(
        f"""<MeteringData>
          <Community><CommunityID>SYNTHETIC-EXTERNAL-LEG</CommunityID></Community>
          <ConsumptionMeteringPoint><VSENationalID>SYNTHETIC-METER-1</VSENationalID></ConsumptionMeteringPoint>
          <Product><ID><VSENationalCode>{code}</VSENationalCode></ID><MeasureUnit>KWH</MeasureUnit></Product>
          <Interval><StartDateTime>2026-09-30T22:00:00Z</StartDateTime><EndDateTime>2026-09-30T22:15:00Z</EndDateTime></Interval>
          <Resolution><Resolution>15</Resolution><Unit>MIN</Unit></Resolution>
          <Observation><Position><Sequence>1</Sequence></Position><Volume>{value}</Volume></Observation>
        </MeteringData>"""
        for code, value in (
            ("8716867000030", "1.000"),
            ("2404050010123", local),
            ("2404050010124", residual),
        )
    )
    return f"""<?xml version="1.0" encoding="utf-8"?>
    <ValidatedMeteredData_16>
      <ValidatedMeteredData_HeaderInformation>
        <InstanceDocument><DocumentID>SYNTHETIC-E66-1</DocumentID><DocumentType><ebIXCode>E66</ebIXCode></DocumentType><Status>{status}</Status><Creation>2026-10-01T04:00:00Z</Creation></InstanceDocument>
        <Sender><ID><EICID>SYNTHETIC-VNB</EICID></ID></Sender>
        <Receiver><ID><EICID>SYNTHETIC-CEM</EICID></ID></Receiver>
        <BusinessScopeProcess><ReportPeriod><StartDateTime>2026-09-30T22:00:00Z</StartDateTime><EndDateTime>2026-09-30T22:15:00Z</EndDateTime></ReportPeriod></BusinessScopeProcess>
      </ValidatedMeteredData_HeaderInformation>{blocks}
    </ValidatedMeteredData_16>""".encode()


@pytest.fixture
def configured_e66(assignment_request):
    client, db, payload, point, statement = assignment_request
    assert (
        client.post(
            f"/admin/vnb-calculated-values/dietikon/mappings/{point}",
            headers={"X-Admin-Token": "synthetic-admin"},
            json=statement,
        ).status_code
        == 201
    )
    return client, db, payload, synthetic_e66()


def test_complete_raw_e66_becomes_immutable_billing_evidence(configured_e66):
    client, db, payload, source = configured_e66
    response = client.post(
        "/admin/vnb-calculated-values/dietikon/e66",
        headers={"X-Admin-Token": "synthetic-admin"},
        data=source,
        content_type="application/xml",
    )

    assert response.status_code == 201
    result = response.get_json()
    assert result["status"] == "accepted"
    assert result["format_version"] == "e66/1"
    assert result["evidence_sha256"] == hashlib.sha256(source).hexdigest()
    assert "evidence_bytes" not in result
    assert response.headers["Cache-Control"] == "no-store"
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM vnb_calculated_values_deliveries")
        delivery = dict(cur.fetchone())
        cur.execute("SELECT * FROM metering_point_readings")
        physical = dict(cur.fetchone())
        cur.execute("SELECT * FROM sdat_imports")
        imported = dict(cur.fetchone())
    assert bytes(delivery["evidence_bytes"]) == source
    assert delivery["source_metadata"]["document_status"] == "9"
    assert delivery["source_metadata"]["mappings"][0]["building_id"] == "member"
    assert delivery["source_metadata"]["precision"] == "0.001 kWh"
    assert float(physical["total_kwh"]) == 1
    assert float(physical["community_kwh"]) == 0.25
    assert float(physical["grid_kwh"]) == 0.75
    assert (
        physical["source_document_id"] == imported["document_id"] == "SYNTHETIC-E66-1"
    )
    reference = vnb_calculated_values.load_billing_reference(
        payload["community_id"],
        datetime.fromisoformat(payload["period_start"]),
        datetime.fromisoformat(payload["period_end"]),
    )
    assert reference["per_participant"]["member"]["consumption_kwh"] == 0.25
    assert reference["evidence_fingerprint"] == result["fingerprint"]


def post_e66(client, source, *, territory="dietikon", token="synthetic-admin"):
    return client.post(
        f"/admin/vnb-calculated-values/{territory}/e66",
        headers={"X-Admin-Token": token} if token else {},
        data=source,
        content_type="application/xml",
    )


@pytest.mark.parametrize(
    "problem",
    [
        "total_only",
        "quality_on_later_channel",
        "duplicate_channel",
        "missing_observation",
        "duplicate_observation",
        "wrong_unit",
        "non_quarter_hour",
        "bad_precision",
        "context_rounded_precision",
        "excess_magnitude",
        "negative",
        "non_finite",
        "mixed_communities",
        "missing_community",
        "unbalanced",
        "cancellation",
        "naive_period",
        "naive_block",
        "missing_status",
        "unknown_product",
        "two_point_roles",
        "duplicate_header_status",
        "duplicate_product_id",
        "duplicate_community_id",
    ],
)
def test_unsafe_raw_e66_leaves_no_evidence_or_physical_rows(configured_e66, problem):
    client, db, _, source = configured_e66
    root = ET.fromstring(source)
    blocks = root.findall("MeteringData")
    if problem == "total_only":
        for block in blocks[1:]:
            root.remove(block)
    elif problem == "quality_on_later_channel":
        ET.SubElement(blocks[-1].find("Observation"), "Condition").text = "21"
    elif problem == "duplicate_channel":
        root.append(ET.fromstring(ET.tostring(blocks[0])))
    elif problem == "missing_observation":
        blocks[-1].remove(blocks[-1].find("Observation"))
    elif problem == "duplicate_observation":
        blocks[-1].append(ET.fromstring(ET.tostring(blocks[-1].find("Observation"))))
    elif problem == "wrong_unit":
        blocks[-1].find("Product/MeasureUnit").text = "WH"
    elif problem == "non_quarter_hour":
        blocks[-1].find("Resolution/Resolution").text = "30"
    elif problem in {
        "bad_precision",
        "context_rounded_precision",
        "excess_magnitude",
        "negative",
        "non_finite",
    }:
        blocks[-1].find("Observation/Volume").text = {
            "bad_precision": "0.7501",
            "context_rounded_precision": "0.75000000000000000000000000001",
            "excess_magnitude": "1E+999999999",
            "negative": "-0.750",
            "non_finite": "NaN",
        }[problem]
    elif problem == "mixed_communities":
        blocks[-1].find("Community/CommunityID").text = "SYNTHETIC-OTHER-LEG"
    elif problem == "missing_community":
        blocks[-1].remove(blocks[-1].find("Community"))
    elif problem == "unbalanced":
        blocks[-1].find("Observation/Volume").text = "0.700"
    elif problem == "cancellation":
        root.find(".//InstanceDocument/Status").text = "1"
    elif problem == "naive_period":
        root.find(".//ReportPeriod/StartDateTime").text = "2026-09-30T22:00:00"
    elif problem == "naive_block":
        blocks[-1].find("Interval/StartDateTime").text = "2026-09-30T22:00:00"
    elif problem == "missing_status":
        instance = root.find(".//InstanceDocument")
        instance.remove(instance.find("Status"))
    elif problem == "unknown_product":
        blocks[-1].find("Product/ID/VSENationalCode").text = "9999999999999"
    elif problem == "two_point_roles":
        ET.SubElement(
            ET.SubElement(blocks[-1], "ProductionMeteringPoint"), "VSENationalID"
        ).text = "SYNTHETIC-PRODUCER"
    elif problem == "duplicate_header_status":
        ET.SubElement(root.find(".//InstanceDocument"), "Status").text = "1"
    elif problem == "duplicate_product_id":
        ET.SubElement(blocks[-1].find("Product/ID"), "ebIXCode").text = "8716867000030"
    elif problem == "duplicate_community_id":
        ET.SubElement(
            blocks[-1].find("Community"), "CommunityID"
        ).text = "SYNTHETIC-OTHER-LEG"

    response = post_e66(client, ET.tostring(root))

    assert response.status_code == 422
    assert response.headers["Cache-Control"] == "no-store"
    with db.get_connection() as conn, conn.cursor() as cur:
        for table in (
            "vnb_calculated_values_deliveries",
            "metering_point_readings",
            "sdat_imports",
        ):
            cur.execute(f"SELECT count(*) AS count FROM {table}")
            assert cur.fetchone()["count"] == 0


@pytest.mark.parametrize(
    "token,territory,expected",
    [
        (None, "dietikon", 403),
        ("wrong", "dietikon", 403),
        ("synthetic-admin", "baden", 422),
    ],
)
def test_raw_e66_cannot_cross_the_authenticated_tenant(
    configured_e66, token, territory, expected
):
    client, db, _, source = configured_e66
    assert (
        post_e66(client, source, token=token, territory=territory).status_code
        == expected
    )
    assert db.list_calculated_values_deliveries("dietikon") == []


@pytest.mark.parametrize("changed_binding", [False, True])
def test_raw_e66_replay_retains_withdrawal_and_original_mapping(
    configured_e66, changed_binding
):
    client, db, _, source = configured_e66
    original = post_e66(client, source).get_json()
    assert (
        client.post(
            f"/admin/vnb-calculated-values/dietikon/{original['id']}/withdrawals",
            headers={
                "X-Admin-Token": "synthetic-admin",
                "Idempotency-Key": "e66-replay-fixture",
            },
            json={"reason": "Synthetic corrected source expected"},
        ).status_code
        == 201
    )
    if changed_binding:
        assert (
            db.upsert_metering_points(
                [
                    {
                        "metering_point_id": "SYNTHETIC-METER-1",
                        "vnb_community_id": "SYNTHETIC-NEW-LEG",
                    }
                ]
            )
            == 1
        )
    response = post_e66(client, source)
    assert response.status_code == 200
    replay = response.get_json()
    assert replay["replayed"] is True
    assert replay["lifecycle_state"] == "withdrawn"
    assert replay["fingerprint"] == original["fingerprint"]
    assert replay["source_metadata"] == original["source_metadata"]


@pytest.mark.parametrize("failing_table", ["metering_point_readings", "sdat_imports"])
def test_raw_e66_database_failure_rolls_back_all_three_stores(
    configured_e66, failing_table, caplog
):
    client, db, _, source = configured_e66
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"ALTER TABLE {failing_table} ADD CONSTRAINT synthetic_failure CHECK (FALSE)"
        )

    response = post_e66(client, source)

    assert response.status_code == 503
    assert response.get_json() == {"error": "calculated_values_unavailable"}
    assert "SYNTHETIC-METER-1" not in caplog.text
    with db.get_connection() as conn, conn.cursor() as cur:
        for table in (
            "vnb_calculated_values_deliveries",
            "metering_point_readings",
            "sdat_imports",
        ):
            cur.execute(f"SELECT count(*) AS count FROM {table}")
            assert cur.fetchone()["count"] == 0


def test_raw_e66_reaches_draft_and_immutable_invoice_provenance(
    configured_e66, monkeypatch
):
    client, db, payload, source = configured_e66
    # The fixture has already created and initialized its isolated database.
    monkeypatch.setattr(db, "_db_initialized", True)
    cid = payload["community_id"]
    assert db.save_building(
        "producer",
        "producer@example.invalid",
        {"address": "Synthetic producer", "lat": 47.2, "lon": 8.2},
        {},
        verified=True,
        city_id="dietikon",
    )
    assert db.insert_invited_member(cid, "producer", "member")
    assert db.confirm_invited_member(cid, "producer")
    assert (
        db.upsert_metering_points(
            [
                {
                    "metering_point_id": "SYNTHETIC-PRODUCER",
                    "community_id": cid,
                    "building_id": "producer",
                    "vnb_community_id": "SYNTHETIC-EXTERNAL-LEG",
                    "expected_directions": ["production"],
                }
            ]
        )
        == 1
    )
    assert (
        client.post(
            "/admin/vnb-calculated-values/dietikon/mappings/SYNTHETIC-PRODUCER",
            headers={"X-Admin-Token": "synthetic-admin"},
            json={
                "effective_from": "2026-09-01T00:00:00Z",
                "effective_until": None,
                "source_reference": "SYNTHETIC-PRODUCER-MEMBERSHIP",
                "confirmed_membership": True,
            },
        ).status_code
        == 201
    )
    root = ET.fromstring(source)
    for block in list(root.findall("MeteringData")):
        duplicate = ET.fromstring(ET.tostring(block))
        point = duplicate.find("ConsumptionMeteringPoint")
        point.tag = "ProductionMeteringPoint"
        point.find("VSENationalID").text = "SYNTHETIC-PRODUCER"
        code = duplicate.find("Product/ID/VSENationalCode").text
        duplicate.find("Observation/Volume").text = (
            "0.000" if code == "2404050010124" else "0.250"
        )
        root.append(duplicate)
    source = ET.tostring(root)
    delivery = post_e66(client, source)
    assert delivery.status_code == 201
    accepted = delivery.get_json()
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE communities SET status = 'active' WHERE community_id = %s", (cid,)
        )
    db.save_billing_policy(
        cid,
        {
            "effective_from": "2026-09-01",
            "internal_price_chf_per_kwh": "0.12",
            "grid_fee_chf_per_kwh": "0.08",
            "settlement_fee_chf_per_kwh": "0",
            "network_level": "same",
            "distribution_model": "proportional",
            "vat_mode": "none",
            "vat_rate_pct": "0",
            "payment_days": 30,
            "invoice_prefix": "SYNTH",
            "delivery_method": "download",
        },
    )
    start, end = (
        datetime.fromisoformat(payload[key]).astimezone(ZoneInfo("Europe/Zurich"))
        for key in ("period_start", "period_end")
    )

    created = billing_runner.run_billing_period(cid, start, end, prepared_by="member")
    assert created["status"] == "created"
    draft = db.get_billing_period(created["period_id"], cid)
    assert draft["calculated_values_fingerprint"] == accepted["fingerprint"]
    frozen_source = draft["reconciliation"]["source_evidence"]
    assert frozen_source["evidence_sha256"] == hashlib.sha256(source).hexdigest()
    assert frozen_source["fingerprint"] == accepted["fingerprint"]
    assert frozen_source["metadata"] == accepted["source_metadata"]
    invoices = db.approve_billing_period(
        created["period_id"], cid, approver_id="producer"
    )
    assert len(invoices) == 2
    for invoice in invoices:
        own_reconciliation = invoice["provenance_snapshot"]["reconciliation"]
        own_source = own_reconciliation["source_evidence"]
        assert (
            not {"registry_series_sha256", "live_overlaps_sha256"}
            & own_source["metadata"].keys()
        )
        assert set(own_reconciliation["per_participant"]) == {invoice["participant_id"]}
        assert set(own_reconciliation["production_per_participant"]) == {
            invoice["participant_id"]
        }
        assert own_source["fingerprint"] == frozen_source["fingerprint"]
        assert own_source["evidence_sha256"] == frozen_source["evidence_sha256"]
        assert {
            mapping["building_id"] for mapping in own_source["metadata"]["mappings"]
        } == {invoice["participant_id"]}
    energy = {
        (item["participant_id"], item["item_type"]): float(item["quantity_kwh"])
        for invoice in invoices
        for item in invoice["line_items_snapshot"]
        if item["item_type"] in {"consumer_charge", "producer_credit"}
    }
    assert energy[("member", "consumer_charge")] == 0.25
    assert energy[("producer", "producer_credit")] == 0.25
    assert all(
        quantity == 0
        for key, quantity in energy.items()
        if key not in {("member", "consumer_charge"), ("producer", "producer_credit")}
    )


def test_e66_correction_requires_withdrawal_and_keeps_lineage(configured_e66):
    client, db, _, source = configured_e66
    original = post_e66(client, source).get_json()
    corrected = synthetic_e66(status="5", local="0.300", residual="0.700").replace(
        b"SYNTHETIC-E66-1", b"SYNTHETIC-E66-2"
    )
    before_withdrawal = post_e66(client, corrected)
    assert before_withdrawal.status_code == 422
    assert (
        before_withdrawal.get_json()["error"] == "e66_replacement_requires_withdrawal"
    )
    assert (
        client.post(
            f"/admin/vnb-calculated-values/dietikon/{original['id']}/withdrawals",
            headers={
                "X-Admin-Token": "synthetic-admin",
                "Idempotency-Key": "e66-correction-fixture",
            },
            json={"reason": "Synthetic VNB correction"},
        ).status_code
        == 201
    )

    response = post_e66(client, corrected)

    assert response.status_code == 201
    accepted = response.get_json()
    assert accepted["source_metadata"]["previously_withdrawn_deliveries"] == [
        {
            "id": original["id"],
            "content_fingerprint": original["fingerprint"],
        }
    ]
    assert (
        post_e66(client, corrected).get_json()["fingerprint"] == accepted["fingerprint"]
    )
    assert post_e66(client, source).get_json()["lifecycle_state"] == "withdrawn"
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT community_kwh, source_document_id FROM metering_point_readings"
        )
        physical = dict(cur.fetchone())
        cur.execute("SELECT count(*) AS count FROM sdat_imports")
        assert cur.fetchone()["count"] == 2
    assert float(physical["community_kwh"]) == 0.3
    assert physical["source_document_id"] == "SYNTHETIC-E66-2"


def test_overlapping_e66_does_not_replace_physical_data(configured_e66):
    client, db, _, source = configured_e66
    assert post_e66(client, source).status_code == 201
    conflict = synthetic_e66(local="0.300", residual="0.700").replace(
        b"SYNTHETIC-E66-1", b"SYNTHETIC-E66-OTHER"
    )

    response = post_e66(client, conflict)

    assert response.status_code == 422
    assert {
        diagnostic["code"] for diagnostic in response.get_json()["diagnostics"]
    } == {"overlapping_period"}
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT community_kwh, source_document_id FROM metering_point_readings"
        )
        physical = dict(cur.fetchone())
        cur.execute("SELECT count(*) AS count FROM sdat_imports")
        assert cur.fetchone()["count"] == 1
    assert float(physical["community_kwh"]) == 0.25
    assert physical["source_document_id"] == "SYNTHETIC-E66-1"


def test_rejected_e66_replay_still_has_an_error_status(configured_e66):
    client, _, _, source = configured_e66
    assert post_e66(client, source).status_code == 201
    conflict = source.replace(b"SYNTHETIC-E66-1", b"SYNTHETIC-E66-OTHER")
    first = post_e66(client, conflict)
    assert first.status_code == 422

    replay = post_e66(client, conflict)

    assert replay.status_code == 422
    assert replay.get_json()["fingerprint"] == first.get_json()["fingerprint"]


def test_refused_original_can_retry_after_overlap_is_withdrawn(configured_e66):
    client, db, _, source = configured_e66
    original = post_e66(client, source).get_json()
    conflict = source.replace(b"SYNTHETIC-E66-1", b"SYNTHETIC-E66-OTHER")
    refused = post_e66(client, conflict)
    assert refused.status_code == 422
    assert (
        client.post(
            f"/admin/vnb-calculated-values/dietikon/{original['id']}/withdrawals",
            headers={
                "X-Admin-Token": "synthetic-admin",
                "Idempotency-Key": "retry-source",
            },
            json={"reason": "Synthetic original withdrawn"},
        ).status_code
        == 201
    )

    retried = post_e66(client, conflict)

    assert retried.status_code == 201
    assert retried.get_json()["fingerprint"] != refused.get_json()["fingerprint"]
    history = db.list_calculated_values_deliveries("dietikon")
    assert len(history) == 3
    assert (
        next(row for row in history if row["id"] == refused.get_json()["id"])["status"]
        == "rejected"
    )


def test_refused_e66_can_retry_after_registry_coverage_is_repaired(configured_e66):
    client, db, payload, source = configured_e66
    assert (
        db.upsert_metering_points(
            [
                {
                    "metering_point_id": "SYNTHETIC-STALE-METER",
                    "community_id": payload["community_id"],
                    "building_id": "member",
                    "expected_directions": ["production"],
                }
            ]
        )
        == 1
    )
    refused = post_e66(client, source)
    assert refused.status_code == 422
    assert {row["code"] for row in refused.get_json()["diagnostics"]} == {
        "incomplete_period"
    }
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE metering_points SET active = FALSE WHERE metering_point_id = %s",
            ("SYNTHETIC-STALE-METER",),
        )

    retried = post_e66(client, source)

    assert retried.status_code == 201
    assert retried.get_json()["fingerprint"] != refused.get_json()["fingerprint"]
    assert post_e66(client, source).get_json()["id"] == retried.get_json()["id"]
    history = db.list_calculated_values_deliveries("dietikon")
    assert len(history) == 2
    assert (
        next(row for row in history if row["id"] == refused.get_json()["id"])["status"]
        == "partially_invalid"
    )


def test_e66_document_identity_must_fit_the_physical_import_ledger(configured_e66):
    client, db, _, source = configured_e66
    source = source.replace(b"SYNTHETIC-E66-1", b"S" * 65)

    response = post_e66(client, source)

    assert response.status_code == 422
    assert response.get_json() == {"error": "missing_e66_provenance"}
    assert db.list_calculated_values_deliveries("dietikon") == []


def test_refused_e66_can_retry_after_a_different_overlapping_window_is_withdrawn(
    configured_e66,
):
    client, db, _, source = configured_e66
    original = post_e66(client, source).get_json()
    root = ET.fromstring(source)
    root.find(".//InstanceDocument/DocumentID").text = "SYNTHETIC-E66-WIDER"
    end = "2026-09-30T22:30:00Z"
    root.find(".//ReportPeriod/EndDateTime").text = end
    for block in root.findall("MeteringData"):
        block.find("Interval/EndDateTime").text = end
        second = ET.fromstring(ET.tostring(block.find("Observation")))
        second.find("Position/Sequence").text = "2"
        block.append(second)
    wider = ET.tostring(root)
    refused = post_e66(client, wider)
    assert refused.status_code == 422
    assert (
        client.post(
            f"/admin/vnb-calculated-values/dietikon/{original['id']}/withdrawals",
            headers={
                "X-Admin-Token": "synthetic-admin",
                "Idempotency-Key": "retry-wider",
            },
            json={"reason": "Synthetic overlapping source withdrawn"},
        ).status_code
        == 201
    )

    retried = post_e66(client, wider)

    assert retried.status_code == 201
    assert retried.get_json()["fingerprint"] != refused.get_json()["fingerprint"]
    assert len(db.list_calculated_values_deliveries("dietikon")) == 3


@pytest.mark.parametrize("operation", ["e66", "mapping"])
def test_unexpected_import_validation_text_stays_private(
    configured_e66, monkeypatch, caplog, operation
):
    import e66_evidence

    client, db, _, source = configured_e66
    marker = "PRIVATE-SYNTHETIC-VALIDATION-DETAIL"

    def fail(*_args):
        exception = e66_evidence.E66EvidenceError if operation == "e66" else ValueError
        raise exception(marker)

    if operation == "e66":
        monkeypatch.setattr(db, "ingest_e66_calculated_values", fail)
        response = post_e66(client, source)
        assert response.status_code == 422
        assert response.get_json() == {"error": "invalid_e66"}
    else:
        monkeypatch.setattr(vnb_calculated_values, "record_meter_assignment", fail)
        response = client.post(
            "/admin/vnb-calculated-values/dietikon/mappings/SYNTHETIC-METER-1",
            headers={"X-Admin-Token": "synthetic-admin"},
            json={},
        )
        assert response.status_code == 400
        assert response.get_json() == {"error": "invalid_calculated_values_request"}
    assert marker not in response.text + caplog.text
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize(
    "local_start,intervals", [("2026-03-29T00:00:00", 92), ("2026-10-25T00:00:00", 100)]
)
def test_e66_maps_complete_swiss_dst_days_to_utc_intervals(
    configured_e66, local_start, intervals
):
    client, db, payload, source = configured_e66
    assert (
        client.post(
            "/admin/vnb-calculated-values/dietikon/mappings/SYNTHETIC-METER-1",
            headers={"X-Admin-Token": "synthetic-admin"},
            json={
                "effective_from": "2026-01-01T00:00:00Z",
                "source_reference": "SYNTHETIC-YEAR-MEMBERSHIP",
                "confirmed_membership": True,
            },
        ).status_code
        == 201
    )
    start = datetime.fromisoformat(local_start).replace(
        tzinfo=ZoneInfo("Europe/Zurich")
    )
    end = start + timedelta(days=1)
    start_utc, end_utc = start.astimezone(UTC), end.astimezone(UTC)
    root = ET.fromstring(source)
    for period in [
        root.find(".//ReportPeriod"),
        *root.findall("MeteringData/Interval"),
    ]:
        period.find("StartDateTime").text = start_utc.isoformat()
        period.find("EndDateTime").text = end_utc.isoformat()
    for block in root.findall("MeteringData"):
        original_observation = ET.tostring(block.find("Observation"))
        block.remove(block.find("Observation"))
        for sequence in range(1, intervals + 1):
            observation = ET.fromstring(original_observation)
            observation.find("Position/Sequence").text = str(sequence)
            block.append(observation)

    response = post_e66(client, ET.tostring(root))

    assert response.status_code == 201
    assert response.get_json()["record_count"] == intervals
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) AS count, min(measured_at) AS start, max(measured_at) AS last FROM metering_point_readings"
        )
        physical = dict(cur.fetchone())
    assert physical == {
        "count": intervals,
        "start": start_utc,
        "last": end_utc - timedelta(minutes=15),
    }
    evidence = db.get_validated_calculated_values(payload["community_id"], start, end)
    assert (
        len({record["measured_at"] for record in evidence["normalized_records"]})
        == intervals
    )


def test_e66_import_and_registry_rebinding_keep_a_frozen_assignment(
    configured_e66, monkeypatch
):
    client, db, _, source = configured_e66
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("""CREATE FUNCTION synthetic_pause_e66() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN
                PERFORM pg_advisory_xact_lock(218786916);
                RETURN NEW;
            END; $$;
            CREATE TRIGGER synthetic_pause_e66 BEFORE INSERT
            ON vnb_calculated_values_deliveries FOR EACH ROW
            EXECUTE FUNCTION synthetic_pause_e66();""")
    with db.get_connection() as control, control.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_lock(218786916), pg_backend_pid() AS pid")
        blocker = lifecycle.SimpleNamespace(pid=cursor.fetchone()["pid"])
        with lifecycle.test_interest_postgres.transaction_race(monkeypatch) as race:
            importing = race.start(
                lambda: post_e66(client.application.test_client(), source)
            )
            try:
                race.wait_until_blocked_by(importing, blocker)
                rebinding = race.start(
                    lambda: db.upsert_metering_points(
                        [
                            {
                                "metering_point_id": "SYNTHETIC-METER-1",
                                "vnb_community_id": "SYNTHETIC-NEW-LEG",
                            }
                        ]
                    )
                )
                race.wait_until_blocked_by(rebinding, importing)
            finally:
                cursor.execute("SELECT pg_advisory_unlock(218786916)")
            original = race.finish(importing)
            assert original.status_code == 201
            assert race.finish(rebinding) == 1
    assert (
        db.get_metering_point("SYNTHETIC-METER-1")["assignment_effective_from"] is None
    )
    replay = post_e66(client, source)
    assert replay.status_code == 200
    assert (
        replay.get_json()["source_metadata"]["mappings"][0]["vnb_community_id"]
        == "SYNTHETIC-EXTERNAL-LEG"
    )


def test_raw_e66_format_cannot_be_claimed_through_unvalidated_json(configured_e66):
    client, db, payload, _ = configured_e66
    response = client.post(
        "/admin/vnb-calculated-values/dietikon",
        headers={"X-Admin-Token": "synthetic-admin"},
        json={
            "delivery": {
                **payload,
                "format_version": "e66/1",
                "source_metadata": {"document_status": "9"},
            }
        },
    )
    assert response.status_code == 422
    assert response.get_json()["status"] == "rejected"
    assert {item["code"] for item in response.get_json()["diagnostics"]} == {
        "unsupported_format"
    }
    start, end = (
        datetime.fromisoformat(payload[key]) for key in ("period_start", "period_end")
    )
    assert (
        db.get_validated_calculated_values(payload["community_id"], start, end) is None
    )


@pytest.mark.parametrize(
    "problem",
    [
        "unknown_point",
        "unconfirmed",
        "inactive",
        "no_participant",
        "no_validity",
        "starts_late",
        "ends_early",
        "tenant_moved",
        "wrong_direction",
    ],
)
def test_e66_requires_a_current_explicit_assignment_for_the_whole_period(
    configured_e66, problem
):
    client, db, _, source = configured_e66
    with db.get_connection() as conn, conn.cursor() as cur:
        if problem == "unconfirmed":
            cur.execute("UPDATE community_members SET status = 'invited'")
        elif problem == "inactive":
            cur.execute("UPDATE metering_points SET active = FALSE")
        elif problem == "no_participant":
            cur.execute("UPDATE metering_points SET building_id = NULL")
        elif problem == "no_validity":
            cur.execute(
                "UPDATE metering_points SET assignment_effective_from = NULL, assignment_effective_until = NULL, assignment_reference = NULL, assignment_actor = NULL, assignment_recorded_at = NULL"
            )
        elif problem == "starts_late":
            cur.execute(
                "UPDATE metering_points SET assignment_effective_from = '2026-09-30T22:01:00Z'"
            )
        elif problem == "ends_early":
            cur.execute(
                "UPDATE metering_points SET assignment_effective_until = '2026-09-30T22:14:00Z'"
            )
        elif problem == "tenant_moved":
            cur.execute("UPDATE buildings SET city_id = 'baden'")
        elif problem == "wrong_direction":
            cur.execute(
                "UPDATE metering_points SET expected_directions = ARRAY['production']::VARCHAR[]"
            )
    if problem == "unknown_point":
        source = source.replace(b"SYNTHETIC-METER-1", b"SYNTHETIC-UNKNOWN-POINT")

    response = post_e66(client, source)

    assert response.status_code == 422
    assert db.list_calculated_values_deliveries("dietikon") == []
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) AS count FROM metering_point_readings")
        assert cur.fetchone()["count"] == 0


def test_later_e66_replacement_can_retry_after_its_current_source_is_withdrawn(
    configured_e66,
):
    client, db, _, source = configured_e66
    original = post_e66(client, source).get_json()

    def withdraw(delivery, key):
        response = client.post(
            f"/admin/vnb-calculated-values/dietikon/{delivery['id']}/withdrawals",
            headers={"X-Admin-Token": "synthetic-admin", "Idempotency-Key": key},
            json={"reason": "Synthetic correction"},
        )
        assert response.status_code == 201

    withdraw(original, "first")
    second_source = synthetic_e66(status="5", local="0.300", residual="0.700").replace(
        b"SYNTHETIC-E66-1", b"SYNTHETIC-E66-2"
    )
    second_response = post_e66(client, second_source)
    assert second_response.status_code == 201
    second = second_response.get_json()
    third_source = synthetic_e66(status="5", local="0.350", residual="0.650").replace(
        b"SYNTHETIC-E66-1", b"SYNTHETIC-E66-3"
    )
    assert post_e66(client, third_source).status_code == 422
    withdraw(second, "second")

    retry = post_e66(client, third_source)

    assert retry.status_code == 201
    accepted = retry.get_json()
    assert accepted["status"] == "accepted"
    assert {
        item["id"]
        for item in accepted["source_metadata"]["previously_withdrawn_deliveries"]
    } == {original["id"], second["id"]}
    assert (
        post_e66(client, third_source).get_json()["fingerprint"]
        == accepted["fingerprint"]
    )
    with db.get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT status FROM vnb_calculated_values_deliveries")
        assert [row["status"] for row in cur.fetchall()] == ["accepted"] * 3


@pytest.mark.parametrize(
    "field", ["Observation/Position/Sequence", "Resolution/Resolution"]
)
def test_malformed_e66_scalars_do_not_leak_source_values_to_logs(
    configured_e66, caplog, field
):
    client, _, _, source = configured_e66
    root = ET.fromstring(source)
    marker = "PRIVATE-SYNTHETIC-METER-IDENTIFIER"
    root.find("MeteringData/" + field).text = marker
    caplog.clear()

    response = post_e66(client, ET.tostring(root))

    assert response.status_code == 422
    assert response.get_json() == {"error": "invalid_e66"}
    assert marker not in caplog.text + response.text
