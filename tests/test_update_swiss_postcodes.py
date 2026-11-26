# SPDX-License-Identifier: AGPL-3.0-or-later
"""Postcode shares from the official building address directory."""

from scripts.update_swiss_postcodes import build


def _addresses(postcode, locality, bfs, count, status="real"):
    return [
        {
            "ZIP_LABEL": f"{postcode} {locality}",
            "COM_FOSNR": str(bfs),
            "ADR_STATUS": status,
        }
    ] * count


def test_shares_follow_the_existing_addresses_of_each_postcode():
    rows = [
        *_addresses("6110", "Wolhusen", 1107, 1313),
        *_addresses("6110", "Wolhusen", 1107, 36, status="planned"),
        *_addresses("6110", "Wolhusen", 1009, 331),
        *_addresses("6110", "Wolhusen", 1098, 78),
        *_addresses("6110", "Wolhusen", 1007, 58),
        *_addresses("6110", "Wolhusen", 1002, 6),
    ]
    assert build(rows).splitlines() == [
        "1007;6110;3;",
        "1009;6110;19;Wolhusen",
        "1098;6110;4;",
        "1107;6110;74;Wolhusen",
    ]


def test_a_small_municipality_keeps_its_only_postcode():
    rows = [
        *_addresses("8910", "Affoltern am Albis", 2, 4000),
        *_addresses("8910", "Affoltern am Albis", 10, 3),
        *_addresses("8912", "Obfelden", 10, 900),
    ]
    assert build(rows).splitlines() == [
        "2;8910;100;Affoltern am Albis",
        "10;8912;100;Obfelden",
    ]
