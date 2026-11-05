# SPDX-License-Identifier: AGPL-3.0-or-later
"""Original persona identity cannot be reconstructed from replacement rows."""

import json

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
    class ExistingHousehold:
        def get_building(self, identifier):
            return {"building_id": identifier, "referral_code": "replacement"}

    with pytest.raises(RuntimeError, match="reset"):
        seed_village(ExistingHousehold(), tmp_path)
    assert not (tmp_path / "fixture.json").exists()
