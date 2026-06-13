# Öffentliche PV-Nutzungsdaten

Diese CSVs speisen die Gemeinde-Solarnutzungs-Rangliste.

## Dateien

- `municipality_pv_current_snapshot.csv`: aktueller Snapshot je Gemeinde (2136 Gemeinden).
- `municipality_pv_panel_2016_2025.csv`: 10-Jahres-Panel je Gemeinde und Jahr.

## Kennzahl

`Solarnutzung = installierte PV-Leistung / geschätztes Dachpotenzial * 100`

- Snapshot nutzt `current_total_kw` der aktuell registrierten Anlagen.
- Panel nutzt kumulierte `initial_power_kw` nach Inbetriebnahmejahr.

## Quellen

- BFE Elektrizitätsproduktionsanlagen (installierte Leistung, Inbetriebnahme).
- BFE Sonnendach (geschätztes Dachpotenzial, Nenner).
- BFS Regionalporträts (Einwohner, Dichte, Fläche).

## Caveats

- Deterministisches Matching deckt 246'139 / 320'114 PV-Anlagen ab (76,89 %).
- 23,11 % der Anlagen bleiben ungematcht (ungelöste Orts- oder Fusionsnamen).
- Der Nenner ist eine Schätzung, einzelne Gemeinden überschreiten 100 %.

Aufbereitet aus dem dbm-leg-project (HSLU). Laden via `scripts/load_pv_data.py`.
