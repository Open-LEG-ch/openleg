# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared Swiss canton constants for OpenLEG."""

SWISS_CANTON_OPTIONS = [
    ("all", "Alle Kantone"),
    ("AG", "Aargau"),
    ("AI", "Appenzell Innerrhoden"),
    ("AR", "Appenzell Ausserrhoden"),
    ("BE", "Bern"),
    ("BL", "Basel-Landschaft"),
    ("BS", "Basel-Stadt"),
    ("FR", "Freiburg"),
    ("GE", "Genf"),
    ("GL", "Glarus"),
    ("GR", "Graubünden"),
    ("JU", "Jura"),
    ("LU", "Luzern"),
    ("NE", "Neuenburg"),
    ("NW", "Nidwalden"),
    ("OW", "Obwalden"),
    ("SG", "St. Gallen"),
    ("SH", "Schaffhausen"),
    ("SO", "Solothurn"),
    ("SZ", "Schwyz"),
    ("TG", "Thurgau"),
    ("TI", "Tessin"),
    ("UR", "Uri"),
    ("VD", "Waadt"),
    ("VS", "Wallis"),
    ("ZG", "Zug"),
    ("ZH", "Zürich"),
]

SWISS_CANTONS = {code for code, _ in SWISS_CANTON_OPTIONS if code != "all"}
