# Operations facts matrix: review draft

Status: internal review draft for issue #612. Do not publish or link from a
public page. Commercial and hosting facts marked `human approval required`
are not product claims.

Evidence date: 2026-09-15. This draft describes the repository at commit
`6afea7a`. It does not describe a particular deployment unless stated.

## Reading the matrix

| Status | Meaning |
| --- | --- |
| Shipped | The repository contains the end-to-end product path and tests. |
| Manual | The repository contains the capability, but a person must start or complete the operation. |
| Adapter-specific | The capability works only through the named format or transport. This says nothing about support by a particular VNB. |
| Planned | The repository does not contain the claimed end-to-end path. |
| Human approval required | A responsible owner must confirm the operating or commercial fact before publication. |

These labels describe software capability. They are not availability, support,
response-time or service-level commitments.

## Product capability

| Area | Status | Supported path and version | Authentication and scope | Limit |
| --- | --- | --- | --- | --- |
| LEG formation | Shipped for the OpenLEG workflow; planned for VNB submission | Create a community, invite and confirm members, start formation and generate formation documents | Dashboard session, CSRF protection and community membership or administrator checks | No versioned VNB submission transport is present in this evidence commit. |
| Membership changes | Shipped for invitations and confirmations; planned for VNB exchange | Community invite and confirmation routes | Dashboard session, CSRF protection and community scope | No versioned VNB mutation transport or VNB acknowledgement is present. |
| SDAT metering import | Adapter-specific and shipped; scheduled operation shipped | Swisseldex Datahub over explicit-TLS FTP; ebIX E66 `ValidatedMeteredData_16`; 15-minute values. Scheduled fetch and import is configured per tenant and called through `POST /api/cron/import-sdat`. | Datahub credentials for retrieval; cron secret for the cron route; admin token for schedule management and manual recovery | E31 aggregates are skipped. A listed VNB is not evidence that this connector works with it. No VNB support list is approved. |
| VNB calculated values | Adapter-specific; manual acceptance path shipped | Contract `vnb-calculated-values/1`, format `json/1`; normalized 15-minute kWh allocations with evidence and replay fingerprints | Admin token; territory in the private admin path sets tenant scope | The repository exposes an authenticated operator intake, not a public or general partner API. No automatic VNB transport is present. |
| Billing | Shipped, with human approval | Validated readings feed period allocation, immutable invoice preparation, approval, delivery and correction records | LEG administrator session and community scope | Approval remains a deliberate operator action. This is product behavior, not an accounting or legal assurance. |
| Payment reconciliation | Manual and adapter-specific | Upload ISO 20022 `camt.053` or `camt.054`; deterministic matching identifies matched, unmatched, ambiguous, partial, excess, reversal and mismatch cases | LEG administrator session, CSRF protection and community scope | A person uploads the statement and handles cases that are not an exact automatic match. There is no bank connection. |
| Portability | Shipped | Export, validate, dry-run and transactional restore of `openleg-community-archive/1` JSON. Dataset SHA-256 hashes and typed values support a lossless round trip. An identical restore can be repeated. | Confirmed LEG administrator session, CSRF protection on write operations and community scope | The archive excludes tokens, unrelated profiles, other communities, platform operations and public reference data. Only archive version 1 is accepted. No compatibility promise exists for a future archive version. |
| Public energy-data API | Shipped | HTTP JSON routes below `/api/v1` for selected public energy data | No API key | It does not expose citizen metering data or private LEG operations. |
| Private operator API and signed events | Planned | No versioned private operator API or signed outbound event contract is present in this evidence commit | Not applicable | Admin and cron HTTP routes are operational controls, not a supported partner API. |

## VNB integration register

No VNB integration may be inferred from a LEG's participation, a utility
contact, a sample CSV fixture or use of the Swisseldex Datahub. The repository
contains no approved VNB-by-VNB support register.

| VNB | Formation | Membership changes | Metering | Transport versions | Publication status |
| --- | --- | --- | --- | --- | --- |
| None approved | Unknown | Unknown | Unknown | Unknown | Human approval required before naming any VNB |

## Portability and retention

- The tested archive schema is `openleg-community-archive/1`. Import accepts
  that version only. A future-version compatibility or migration policy is
  not yet approved.
- An archive is a portability copy. Export does not delete source records, and
  restore does not change their retention rules. The administrator must
  protect the exported personal, metering and financial data.
- The implemented deletion and retention behavior is recorded in
  `docs/retention-matrix.md`. Automated retention exists for terminal email
  queue rows after 90 days. Many other domains have no automated deletion
  horizon. The stated 10-year billing horizon is policy, not code.
- The current public privacy text includes unverified hosting-level and
  deletion statements identified in `docs/retention-matrix.md`. It is not
  evidence for processors, data locations or an implemented general retention
  schedule.

## Operating responsibilities

| Responsibility | Self-hosted installation | Managed operation |
| --- | --- | --- |
| Install and configure the application, database, secrets, TLS, backups, monitoring, upgrades and external cron | Operator responsibility | Human approval required |
| Configure VNB/Datahub credentials and tenant mappings | Operator responsibility | Human approval required |
| Protect exported archives and imported bank or metering files | Operator responsibility | Human approval required |
| Infrastructure location, subprocessors and transfer jurisdictions | Chosen by the self-hoster | Human approval required |
| Support channel, hours, response targets and service levels | No repository commitment | Human approval required |
| Fees and included services | AGPL source is available; operating costs and paid help are not defined here | Human approval required |

The repository documents how to run the software. It does not prove the facts
of the current `openleg.ch` deployment or create a managed-service commitment.

# Betriebsmatrix: Entwurf zur Prüfung

Status: interner Prüfentwurf für Issue #612. Nicht veröffentlichen und nicht
von einer öffentlichen Seite verlinken. Angaben zu Angebot und Hosting mit dem
Status `Freigabe durch verantwortliche Person erforderlich` sind keine
Produktversprechen.

Stand der Evidenz: 15.09.2026. Dieser Entwurf beschreibt das Repository beim
Commit `6afea7a`. Er beschreibt keine bestimmte Installation, sofern dies
nicht ausdrücklich angegeben ist.

## So ist die Matrix zu lesen

| Status | Bedeutung |
| --- | --- |
| Ausgeliefert | Das Repository enthält den vollständigen Produktablauf und Tests. |
| Manuell | Das Repository enthält die Funktion, aber eine Person muss den Vorgang starten oder abschliessen. |
| Adapterspezifisch | Die Funktion arbeitet nur mit dem genannten Format oder Transport. Daraus folgt keine Unterstützung durch einen bestimmten VNB. |
| Geplant | Das Repository enthält den behaupteten vollständigen Ablauf nicht. |
| Freigabe durch verantwortliche Person erforderlich | Eine verantwortliche Person muss die Betriebs- oder Angebotsangabe vor der Veröffentlichung bestätigen. |

Diese Bezeichnungen beschreiben Softwarefunktionen. Sie sind keine Zusagen zu
Verfügbarkeit, Support, Reaktionszeiten oder Service Levels.

## Produktfunktionen

| Bereich | Status | Unterstützter Ablauf und Version | Authentifizierung und Geltungsbereich | Grenze |
| --- | --- | --- | --- | --- |
| LEG-Gründung | Für den OpenLEG-Ablauf ausgeliefert; für die Einreichung beim VNB geplant | Gemeinschaft erstellen, Mitglieder einladen und bestätigen, Gründung starten und Gründungsdokumente erzeugen | Dashboard-Sitzung, CSRF-Schutz und Prüfung der Mitgliedschaft oder Administration | Dieser Evidenz-Commit enthält keinen versionierten Transport für die Einreichung beim VNB. |
| Mitgliedschaftsänderungen | Einladungen und Bestätigungen ausgeliefert; Austausch mit dem VNB geplant | Routen für Einladung und Bestätigung in der Gemeinschaft | Dashboard-Sitzung, CSRF-Schutz und Begrenzung auf die Gemeinschaft | Kein versionierter Transport für VNB-Mutationen und keine VNB-Empfangsbestätigung vorhanden. |
| SDAT-Messdatenimport | Adapterspezifisch und ausgeliefert; zeitgesteuerter Betrieb ausgeliefert | Swisseldex Datahub über FTP mit explizitem TLS; ebIX E66 `ValidatedMeteredData_16`; 15-Minuten-Werte. Abruf und Import werden pro Mandant konfiguriert und über `POST /api/cron/import-sdat` aufgerufen. | Datahub-Zugangsdaten für den Abruf; Cron-Secret für die Cron-Route; Admin-Token für Konfiguration und manuelle Wiederholung | E31-Aggregate werden übersprungen. Ein aufgeführter VNB wäre kein Beleg für die Funktion dieses Anschlusses. Es gibt keine freigegebene VNB-Supportliste. |
| Berechnete VNB-Werte | Adapterspezifisch; manueller Annahmeweg ausgeliefert | Vertrag `vnb-calculated-values/1`, Format `json/1`; normalisierte 15-Minuten-Zuteilungen in kWh mit Evidenz und Replay-Fingerprints | Admin-Token; das Gebiet im privaten Admin-Pfad legt den Mandanten fest | Das Repository bietet eine authentifizierte Annahme für Betreiber, keine öffentliche oder allgemeine Partner-API. Es gibt keinen automatischen VNB-Transport. |
| Abrechnung | Ausgeliefert, mit menschlicher Freigabe | Validierte Messwerte speisen Periodenzuteilung, unveränderliche Rechnungsvorbereitung, Freigabe, Zustellung und Korrekturaufzeichnungen | Sitzung der LEG-Administration und Begrenzung auf die Gemeinschaft | Die Freigabe bleibt eine bewusste Handlung der Betreiberin. Das Produktverhalten ist keine buchhalterische oder rechtliche Zusicherung. |
| Zahlungsabgleich | Manuell und adapterspezifisch | Upload von ISO 20022 `camt.053` oder `camt.054`; der deterministische Abgleich erkennt Treffer, fehlende und mehrdeutige Zuordnungen, Teil- und Überzahlungen, Rückbuchungen und Abweichungen | Sitzung der LEG-Administration, CSRF-Schutz und Begrenzung auf die Gemeinschaft | Eine Person lädt den Kontoauszug hoch und bearbeitet Fälle ohne exakte automatische Zuordnung. Es gibt keine Bankanbindung. |
| Portabilität | Ausgeliefert | Export, Prüfung, Testlauf und transaktionale Wiederherstellung von `openleg-community-archive/1` JSON. SHA-256-Prüfsummen pro Datensatz und typisierte Werte ermöglichen einen verlustfreien Rundlauf. Eine identische Wiederherstellung kann wiederholt werden. | Sitzung einer bestätigten LEG-Administration, CSRF-Schutz für Schreibvorgänge und Begrenzung auf die Gemeinschaft | Das Archiv enthält keine Tokens, fremden Profile, anderen Gemeinschaften, Plattform-Betriebsdaten oder öffentlichen Referenzdaten. Nur Archivversion 1 wird akzeptiert. Für eine künftige Archivversion besteht keine Kompatibilitätszusage. |
| Öffentliche Energiedaten-API | Ausgeliefert | HTTP-JSON-Routen unter `/api/v1` für ausgewählte öffentliche Energiedaten | Kein API-Key | Sie gibt keine Messdaten von Personen und keine privaten LEG-Betriebsdaten aus. |
| Private Betreiber-API und signierte Ereignisse | Geplant | Dieser Evidenz-Commit enthält keine versionierte private Betreiber-API und keinen Vertrag für signierte ausgehende Ereignisse | Nicht anwendbar | Admin- und Cron-HTTP-Routen sind Betriebssteuerungen, keine unterstützte Partner-API. |

## Register der VNB-Anbindungen

Eine VNB-Anbindung darf weder aus der Teilnahme an einer LEG noch aus einem
Versorgerkontakt, einer CSV-Testdatei oder der Nutzung des Swisseldex Datahub
abgeleitet werden. Das Repository enthält kein freigegebenes Supportregister
pro VNB.

| VNB | Gründung | Mitgliedschaftsänderungen | Messdaten | Transportversionen | Veröffentlichungsstatus |
| --- | --- | --- | --- | --- | --- |
| Keiner freigegeben | Unbekannt | Unbekannt | Unbekannt | Unbekannt | Vor der Nennung eines VNB ist die Freigabe erforderlich |

## Portabilität und Aufbewahrung

- Das getestete Archivschema ist `openleg-community-archive/1`. Der Import
  akzeptiert nur diese Version. Für künftige Versionen ist noch keine
  Kompatibilitäts- oder Migrationsregel freigegeben.
- Ein Archiv ist eine Portabilitätskopie. Der Export löscht keine
  Quelldatensätze. Die Wiederherstellung ändert deren Aufbewahrungsregeln
  nicht. Die Administration muss die exportierten Personen-, Mess- und
  Finanzdaten schützen.
- Das implementierte Lösch- und Aufbewahrungsverhalten steht in
  `docs/retention-matrix.md`. Für abgeschlossene E-Mail-Warteschlangeneinträge
  besteht eine automatische Frist von 90 Tagen. Viele andere Bereiche haben
  keine automatische Löschfrist. Die genannte zehnjährige Aufbewahrung von
  Abrechnungsdaten ist eine Regel, keine Implementierung.
- Der aktuelle öffentliche Datenschutztext enthält nicht verifizierte Angaben
  zu Hosting und Löschung, die in `docs/retention-matrix.md` festgehalten sind.
  Er belegt weder Auftragsbearbeiter und Datenstandorte noch einen allgemein
  implementierten Aufbewahrungsplan.

## Betriebsverantwortung

| Verantwortung | Selbstbetrieb | Verwalteter Betrieb |
| --- | --- | --- |
| Anwendung, Datenbank, Secrets, TLS, Backups, Monitoring, Aktualisierungen und externen Cron installieren und konfigurieren | Verantwortung der Betreiberin | Freigabe durch verantwortliche Person erforderlich |
| VNB-/Datahub-Zugangsdaten und Mandantenzuordnung konfigurieren | Verantwortung der Betreiberin | Freigabe durch verantwortliche Person erforderlich |
| Exportierte Archive und importierte Bank- oder Messdateien schützen | Verantwortung der Betreiberin | Freigabe durch verantwortliche Person erforderlich |
| Infrastrukturstandort, Auftragsbearbeiter und Übermittlungsstaaten | Wahl des Selbsthosters | Freigabe durch verantwortliche Person erforderlich |
| Supportkanal, Zeiten, Reaktionsziele und Service Levels | Keine Zusage im Repository | Freigabe durch verantwortliche Person erforderlich |
| Preise und enthaltene Leistungen | AGPL-Quellcode ist verfügbar; Betriebskosten und bezahlte Hilfe sind hier nicht festgelegt | Freigabe durch verantwortliche Person erforderlich |

Das Repository beschreibt den Betrieb der Software. Es belegt weder die Fakten
der aktuellen Installation unter `openleg.ch` noch begründet es eine Zusage für
einen verwalteten Dienst.
