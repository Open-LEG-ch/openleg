# SPDX-License-Identifier: AGPL-3.0-or-later
"""Original persona identity cannot be reconstructed from replacement rows."""

import json
from unittest.mock import MagicMock

import pytest

from sandbox_seed import PERSONAS, SEED_VERSION, seed_village
from tests import test_sandbox_http

village = test_sandbox_http.village


@pytest.mark.parametrize("proofs", [None, {}, {"sonnwil-resident": "partial"}])
def test_existing_fixture_without_complete_original_proof_requires_reset(
    tmp_path, proofs
):
    fixture = tmp_path / "fixture.json"
    content = {"version": SEED_VERSION}
    if proofs is not None:
        content["household_identities"] = proofs
    fixture.write_text(json.dumps(content))
    before = fixture.read_bytes()
    with pytest.raises(RuntimeError, match="reset"):
        seed_village(object(), tmp_path)
    assert fixture.read_bytes() == before


def test_existing_fixture_keeps_original_proof_without_reading_current_rows(tmp_path):
    proofs = {identifier: "original-" + identifier for identifier, *_ in PERSONAS}
    fixture = tmp_path / "fixture.json"
    fixture.write_text(
        json.dumps({"version": SEED_VERSION, "household_identities": proofs})
    )
    before = fixture.read_bytes()
    assert seed_village(object(), tmp_path) == proofs
    assert fixture.read_bytes() == before


def test_lost_fixture_cannot_adopt_an_existing_household(tmp_path):
    db = MagicMock()
    cursor = db.get_connection.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = {"has_buildings": True}
    with pytest.raises(RuntimeError, match="reset"):
        seed_village(db, tmp_path)
    db.save_building.assert_not_called()
    assert not (tmp_path / "fixture.json").exists()


def test_lost_fixture_database_failure_cannot_start_seeding(tmp_path):
    db = MagicMock()
    cursor = db.get_connection.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
    cursor.execute.side_effect = RuntimeError("database unavailable")
    with pytest.raises(RuntimeError, match="database unavailable"):
        seed_village(db, tmp_path)
    db.save_building.assert_not_called()
    assert not (tmp_path / "fixture.json").exists()


@pytest.mark.integration
def test_new_village_has_august_draft_and_no_september_source(village):
    from datetime import datetime

    from tests.test_sandbox_metering import _workspace_connection
    from tests.test_sandbox_personas import _start

    client = village.test_client()
    prefix, _ = _start(client, "operator")
    with (
        _workspace_connection(village, prefix) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "SELECT community_id, status, period_start, period_end, source_document_ids "
            "FROM billing_periods"
        )
        periods = cursor.fetchall()
        assert len(periods) == 1
        community, status, start, end, documents = periods[0]
        assert community == "sonnwil-operating-leg"
        assert status == "draft"
        assert start == datetime.fromisoformat("2026-07-31T22:00:00+00:00")
        assert end == datetime.fromisoformat("2026-08-31T22:00:00+00:00")
        assert documents == ["SONNWIL-SIM-2026-08-VALID"]
        cursor.execute(
            "SELECT status, period_start, period_end FROM vnb_calculated_values_deliveries"
        )
        assert cursor.fetchall() == [("accepted", start, end)]
        cursor.execute(
            "SELECT source_metadata, record_count FROM vnb_calculated_values_deliveries"
        )
        metadata, count = cursor.fetchone()
        assert count == 14880
        assert len(metadata["mappings"]) == 5
        for mapping in metadata["mappings"]:
            assert datetime.fromisoformat(mapping["assignment_effective_from"]) == start
            assert datetime.fromisoformat(mapping["assignment_effective_until"]) == end
        cursor.execute(
            "SELECT SUM(total_kwh), SUM(community_kwh), SUM(grid_kwh) FROM metering_point_readings GROUP BY direction ORDER BY direction"
        )
        assert cursor.fetchall() == [(2976, 1488, 1488), (1488, 1488, 0)]
        cursor.execute("SELECT effective_to FROM billing_tariffs")
        assert cursor.fetchone()[0] == end
        cursor.execute("SELECT COUNT(*) FROM invoices")
        assert cursor.fetchone()[0] == 0
    page = client.get(prefix + "/leg/community/sonnwil-operating-leg/billing")
    assert page.status_code == 200
    assert "Entwurf" in page.text
    assert "2026" in page.text


@pytest.mark.integration
def test_seed_keeps_september_exercise_empty_and_august_approvable(village):
    from tests.test_sandbox_http import _csrf
    from tests.test_sandbox_metering import _workspace_connection
    from tests.test_sandbox_personas import _start, _switch

    client = village.test_client()
    prefix, dashboard = _start(client, "operator")
    assert "Gleichverteilt" in dashboard.text
    metering = client.get(prefix + "/sandbox/metering")
    assert "Noch keine angenommene Quelle" in metering.text
    assert "01.09.2026 00:00" in metering.text
    assert "01.10.2026 00:00" in metering.text
    assert "SONNWIL-SIM-2026-08-VALID" not in metering.text
    path = prefix + "/leg/community/sonnwil-operating-leg/billing"
    page = client.get(path)
    assert 'name="confirm_prepare"' in page.text
    assert 'name="confirm_approval"' not in page.text
    with (
        _workspace_connection(village, prefix) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute("SELECT id FROM billing_periods")
        period_id = cursor.fetchone()[0]
    prepare = path + f"/period/{period_id}/prepare"
    assert (
        client.post(
            prepare, data={"csrf_token": _csrf(page), "confirm_prepare": "yes"}
        ).status_code
        == 302
    )
    _switch(client, prefix, "administrator")
    page = client.get(path)
    assert "Entwurf" in page.text
    assert 'name="confirm_approval"' in page.text
    approve = path + f"/period/{period_id}/approve"
    assert client.post(approve, data={"csrf_token": _csrf(page)}).status_code == 400
    response = client.post(
        approve,
        data={"csrf_token": _csrf(page), "confirm_approval": "yes"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Die Rechnungen wurden freigegeben" in response.text
    with (
        _workspace_connection(village, prefix) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute("SELECT status FROM billing_periods")
        assert cursor.fetchone()[0] == "issued"
        cursor.execute(
            "SELECT COUNT(*), MIN(community_id), MIN(invoice_number) FROM invoices"
        )
        count, community, number = cursor.fetchone()
        assert count == 4
        assert community == "sonnwil-operating-leg"
        assert number.startswith("SIMULATION")


def test_previous_seed_version_requires_reset(tmp_path):
    (tmp_path / "fixture.json").write_text(json.dumps({"version": "sonnwil/1"}))
    with pytest.raises(RuntimeError, match="version mismatch"):
        seed_village(object(), tmp_path)
