# SPDX-License-Identifier: AGPL-3.0-or-later
"""Public contracts for the LEG operational archive (#601)."""

import io
import json
from unittest.mock import MagicMock

import pytest

import community_archive
from tests.test_dashboard_access_routes import (  # noqa: F401
    _set_session,
    app_module,
)


class MemoryArchiveStore:
    def __init__(self, datasets=None):
        self.datasets = datasets or {}
        self.restored = None
        self.restore_calls = 0

    def export_community(self, community_id):
        return self.datasets.get(community_id)

    def restore_community(self, datasets):
        self.restore_calls += 1
        self.restored = datasets


def representative_data():
    return {
        "communities": [{"community_id": "leg-1", "name": "Limmat LEG"}],
        "community_members": [
            {
                "id": 4,
                "community_id": "leg-1",
                "building_id": "house-1",
                "role": "admin",
                "status": "confirmed",
            }
        ],
        "data_consents": [{"id": 8, "building_id": "house-1", "tier": 2}],
        "leg_documents": [
            {
                "id": 3,
                "community_id": "leg-1",
                "filename": "vertrag.pdf",
                "pdf_data": b"contract",
            }
        ],
        "metering_points": [{"metering_point_id": "CH-1", "community_id": "leg-1"}],
        "metering_point_readings": [
            {
                "id": 9,
                "metering_point_id": "CH-1",
                "direction": "consumption",
                "total_kwh": "1.2500",
            }
        ],
        "billing_tariffs": [
            {"id": 2, "community_id": "leg-1", "timezone": "Europe/Zurich"}
        ],
        "billing_periods": [{"id": 5, "community_id": "leg-1"}],
        "invoices": [{"id": 7, "community_id": "leg-1", "status": "issued"}],
        "invoice_lifecycle_events": [
            {"id": 11, "invoice_id": 7, "community_id": "leg-1"}
        ],
        "correspondence_log": [
            {
                "id": 12,
                "community_id": "leg-1",
                "attachment_data": b"letter",
            }
        ],
    }


def test_representative_archive_round_trip_preserves_records_and_identifiers():
    source = MemoryArchiveStore({"leg-1": representative_data()})

    archive = community_archive.export_community_archive("leg-1", store=source)
    dry_run = community_archive.restore_community_archive(
        archive, dry_run=True, store=MemoryArchiveStore()
    )
    target = MemoryArchiveStore()
    restored = community_archive.restore_community_archive(archive, store=target)

    payload = json.loads(archive)
    assert payload["manifest"]["schema_version"] == "openleg-community-archive/1"
    assert payload["manifest"]["units"]["energy"] == "kWh"
    assert payload["manifest"]["time_zones"] == ["Europe/Zurich", "UTC"]
    assert dry_run == {"valid": True, "errors": [], "conflicts": []}
    assert restored == {"valid": True, "errors": [], "conflicts": [], "restored": True}
    for name, rows in representative_data().items():
        assert target.restored[name] == rows


def test_dry_run_rejects_hash_mismatch_without_mutation():
    archive = community_archive.export_community_archive(
        "leg-1", store=MemoryArchiveStore({"leg-1": representative_data()})
    )
    payload = json.loads(archive)
    payload["datasets"]["communities"][0]["name"] = "tampered"
    target = MemoryArchiveStore()

    result = community_archive.restore_community_archive(
        json.dumps(payload).encode(), dry_run=True, store=target
    )

    assert result["valid"] is False
    assert result["errors"] == ["Hash mismatch: communities"]
    assert target.restore_calls == 0


def test_unsupported_version_and_target_conflict_are_reported_without_mutation():
    archive = community_archive.export_community_archive(
        "leg-1", store=MemoryArchiveStore({"leg-1": representative_data()})
    )
    payload = json.loads(archive)
    payload["manifest"]["schema_version"] = "openleg-community-archive/99"
    target = MemoryArchiveStore()

    result = community_archive.restore_community_archive(
        json.dumps(payload).encode(), dry_run=True, store=target
    )

    assert result["valid"] is False
    assert result["errors"] == [
        "Unsupported schema version: openleg-community-archive/99"
    ]
    assert target.restore_calls == 0


def test_export_refuses_unknown_community():
    with pytest.raises(community_archive.ArchiveError, match="LEG not found"):
        community_archive.export_community_archive(
            "missing", store=MemoryArchiveStore()
        )


def test_private_archive_routes_require_confirmed_admin(
    app_module,  # noqa: F811
    monkeypatch,
):
    client = app_module.web.test_client()
    _set_session(client, building_id="admin")
    monkeypatch.setattr(
        app_module.dashboard_module,
        "leg_export_archive",
        lambda community_id, building_id: (
            b'{"archive":true}' if building_id == "admin" else None
        ),
    )

    response = client.get("/leg/community/leg-1/archive")

    assert response.status_code == 200
    assert response.mimetype == "application/json"
    assert response.headers["Cache-Control"] == "no-store"
    assert "openleg-leg-1.json" in response.headers["Content-Disposition"]


def test_restore_dry_run_and_restore_use_uploaded_archive(
    app_module,  # noqa: F811
    monkeypatch,
):
    client = app_module.web.test_client()
    _set_session(client, building_id="admin", csrf_token="csrf")
    restore = MagicMock(
        side_effect=[
            {"valid": True, "errors": [], "conflicts": []},
            {"valid": True, "errors": [], "conflicts": [], "restored": True},
        ]
    )
    monkeypatch.setattr(app_module.dashboard_module, "leg_restore_archive", restore)

    dry_run = client.post(
        "/leg/community/leg-1/archive/dry-run",
        data={"csrf_token": "csrf", "archive": (io.BytesIO(b"{}"), "archive.json")},
    )
    restored = client.post(
        "/leg/community/leg-1/archive/restore",
        data={"csrf_token": "csrf", "archive": (io.BytesIO(b"{}"), "archive.json")},
    )

    assert dry_run.get_json()["valid"] is True
    assert restored.get_json()["restored"] is True
    assert restore.call_args_list[0].args == ("leg-1", "admin", b"{}")
    assert restore.call_args_list[0].kwargs == {"dry_run": True}
    assert restore.call_args_list[1].kwargs == {"dry_run": False}
