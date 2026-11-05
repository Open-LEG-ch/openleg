# SPDX-License-Identifier: AGPL-3.0-or-later
"""Original persona identity cannot be reconstructed from replacement rows."""

import json
from unittest.mock import MagicMock

import pytest

from sandbox_seed import PERSONAS, SEED_VERSION, seed_village


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
