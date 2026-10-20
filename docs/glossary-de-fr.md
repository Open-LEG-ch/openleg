# German / Swiss French terminology

Use complete gettext messages with named parameters. Translate display labels,
not database identifiers, machine states, measurement products or stored values.
French uses the `fr_CH` catalog; German remains the default (`de_CH`).

| German source | Swiss French | Scope |
|---|---|---|
| Lokale Elektrizitätsgemeinschaft (LEG) | communauté électrique locale (CEL) | User-facing name; keep IDs unchanged. |
| Gemeinde | commune | Political municipality, distinct from the energy community. |
| Gemeinschaft | communauté | Group or energy community, according to context. |
| Verteilnetzbetreiber (VNB) | gestionnaire de réseau de distribution (GRD) | Distribution operator. |
| Verteilmodell | modèle de répartition | Allocation of energy, not the distribution network. |
| Anschlussleistung | puissance de raccordement | Preserve the source definition and regulatory conditions. |
| Eigenverbrauch | autoconsommation | Distinguish own consumption from shared allocation. |
| Netzeinspeisung | injection dans le réseau | Measured outgoing energy. |
| Netzbezug | soutirage du réseau | Measured incoming energy. |
| Abrechnung | décompte / facturation | Use décompte for a statement, facturation for the process. |
| Abrechnungsrichtlinie | règles de facturation | Versioned billing configuration. |
| Abrechnungsentgelt | frais de décompte / frais de facturation | Retain the specific source's fee basis and unit. |
| Gutschrift | note de crédit / crédit | Financial document versus a displayed credit line. |
| Zahlungsfrist | délai de paiement | Use gettext plurals: 1 jour, 2 jours; 1 Tag, 2 Tage. |
| Messwert | valeur de mesure | Operational telemetry does not replace validated GRD billing data. |
| Hinweis bestätigen | J’ai pris connaissance de ce message | Acknowledgment does not establish that a device fault is resolved. |
| Hinweis bestätigt | Message pris en compte | The notice was acknowledged; its underlying measurement state remains visible. |

## Human validation still required

`Rückbuchung` depends on its banking operation: do not change machine state or
choose between contre-passation and annulation without confirming that meaning.
Contractual clauses, legal deadlines, tariff ceilings, eligibility conditions,
consent scope and tax terms require qualified human review before official use.
This glossary and the French catalog are translation guidance, not legal advice.
