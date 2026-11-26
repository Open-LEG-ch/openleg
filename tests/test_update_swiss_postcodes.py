# SPDX-License-Identifier: AGPL-3.0-or-later
"""Postcode address counts from the official building address directory."""

from scripts.update_swiss_postcodes import build


def _addresses(postcode, locality, bfs, count, status="real"):
    return [
        {
            "ZIP_LABEL": f"{postcode} {locality}",
            "COM_FOSNR": str(bfs),
            "ADR_STATUS": status,
        }
    ] * count


def test_rows_count_the_existing_addresses_of_each_postcode():
    rows = [
        *_addresses("6110", "Wolhusen", 1107, 1313),
        *_addresses("6110", "Wolhusen", 1107, 36, status="planned"),
        *_addresses("6110", "Wolhusen", 1009, 331),
        *_addresses("6110", "Wolhusen", 1098, 78),
        *_addresses("6110", "Wolhusen", 1007, 58),
        *_addresses("6110", "Wolhusen", 1002, 6),
    ]
    assert build(rows).splitlines() == [
        "1007;6110;58;",
        "1009;6110;331;Wolhusen",
        "1098;6110;78;",
        "1107;6110;1313;Wolhusen",
    ]


def test_a_small_municipality_keeps_its_only_postcode():
    rows = [
        *_addresses("8910", "Affoltern am Albis", 2, 4000),
        *_addresses("8910", "Affoltern am Albis", 10, 3),
        *_addresses("8912", "Obfelden", 10, 900),
    ]
    assert build(rows).splitlines() == [
        "2;8910;4000;Affoltern am Albis",
        "10;8912;900;Obfelden",
    ]


def test_close_shares_keep_their_order():
    """3274: Merzligen has 269 existing addresses, Hermrigen 266."""
    from swiss_postcodes import postcodes_by_municipality

    def share(bfs):
        return next(
            share
            for code, share, _names in postcodes_by_municipality()[bfs]
            if code == "3274"
        )

    assert share(741) > share(737)
