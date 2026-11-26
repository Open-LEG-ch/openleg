# SPDX-License-Identifier: AGPL-3.0-or-later
"""Postcode shares from the swisstopo locality list."""

from scripts.update_swiss_postcodes import build


def _row(postcode, zip_id, locality, bfs, share):
    return {
        "PLZ4": postcode,
        "ZIP_ID": zip_id,
        "Ortschaftsname": locality,
        "BFS-Nr": str(bfs),
        "Adressenanteil": f"{share} %",
    }


def test_each_locality_of_a_postcode_weighs_the_same():
    rows = [
        _row("1148", "293", "L'Isle", 5486, "100"),
        _row("1148", "7449", "Villars-Bozon", 5486, "100"),
        _row("1148", "8477", "La Coudre", 5486, "100"),
        _row("1148", "5842", "Mauraz", 5488, "100"),
        _row("1148", "556", "Cuarnens", 5479, "100"),
        _row("1148", "5841", "Chavannes-le-Veyron", 5475, "98.361"),
        _row("1148", "5841", "Chavannes-le-Veyron", 5474, "1.639"),
    ]
    lines = build(rows).splitlines()
    assert lines == [
        "5475;1148;16;Chavannes-le-Veyron",
        "5479;1148;17;Cuarnens",
        "5486;1148;50;L'Isle|La Coudre|Villars-Bozon",
        "5488;1148;17;Mauraz",
    ]


def test_a_tiny_share_of_another_municipality_is_dropped():
    rows = [
        _row("8910", "5141", "Affoltern am Albis", 2, "99.739"),
        _row("8910", "5141", "Affoltern am Albis", 10, "0.261"),
        _row("8912", "5142", "Obfelden", 10, "100"),
    ]
    assert build(rows).splitlines() == [
        "2;8910;100;Affoltern am Albis",
        "10;8912;100;Obfelden",
    ]
