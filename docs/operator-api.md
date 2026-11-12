# Private Betreiber-API v1

Die Betreiber-API bindet jedes Credential an genau eine LEG. Sie dient für
Gründungsanmeldungen, VNB-Mitgliedermutationen sowie Operationen rund um
Messdaten, Abrechnung und Zahlungen; sie ist kein Datenbankexport. Den
detaillierten Vertrag mit allen Endpunkten beschreibt
[operator-operations-api.md](operator-operations-api.md).

## Credentials

Pro LEG sind höchstens 10 aktive Credentials erlaubt. Widerrufene Credentials
zählen nicht mit. Eine Rotation aktiviert ein widerrufenes Credential wieder und
braucht dafür einen freien Platz. Eine Erstellung oder Rotation über diesem
Limit liefert HTTP 409 mit
`{"error":"Credential limit reached","schema_version":"operator-api/1","code":"conflict","retryable":false}`
und ändert keine Daten. Jeder gültige OAuth-Grant belegt ein Credential;
abgelaufene oder widerrufene Grants zählen nicht. Ein OAuth-Tokentausch am Limit
liefert HTTP 400 mit
`{"error":"invalid_grant","error_description":"Credential limit reached"}` und
lässt den Autorisierungscode unverbraucht.

Eine bestätigte LEG-Administration erstellt, liest, rotiert und widerruft
Credentials unter `/leg/community/{community_id}/operator-api/credentials`.
Der API-Token und das Webhook-Signing-Secret werden bei der Erstellung und
Rotation einmal angezeigt. OpenLEG speichert vom API-Token ausschliesslich den
SHA-256-Hash. Das Signing-Secret wird zufällig erzeugt und separat in der
Datenbank gespeichert. Ein Wechsel des Sitzungsschlüssels verändert es nicht.
Eine Credential-Rotation ersetzt Token und Signing-Secret. Der Empfänger muss
das neue Signing-Secret übernehmen. Widerruf wird bei der nächsten HTTP-Anfrage
geprüft.

Bestehende Credentials ohne gespeichertes Signing-Secret zeigen
`webhook_requires_rotation: true`. Ihre Webhooks bleiben bis zur expliziten
Rotation gesperrt. OpenLEG ändert einen bereits installierten Empfängerschlüssel
nicht stillschweigend.

Capabilities sind explizit: `formation.read`, `formation.mutate`,
`membership.read`, `membership.mutate`, `metering.read`, `metering.mutate`,
`billing.read`, `cases.read`, `cases.mutate`, `payments.read` und `payments.mutate`. Ein
Credential erhält keine Capability automatisch. Pro Credential gilt
standardmäßig ein Limit von 100 Anfragen pro Stunde.

## HTTP

Alle Aufrufe verwenden `Authorization: Bearer olk_…` und liefern
`Cache-Control: no-store`. Fremde `community_id` werden als 404 behandelt.

```http
GET /api/operator/v1/communities/leg-123/formation
Authorization: Bearer olk_…
```

```http
POST /api/operator/v1/communities/leg-123/membership-mutations
Authorization: Bearer olk_…
Content-Type: application/json

{"mutation_id":"m-2026-01","participant_id":"p-7","mutation_type":"join","effective_date":"2026-10-01","source_agreement_id":"a-4","after":{"metering_point_id":"CH…"}}
```

Antworten tragen `schema_version: operator-api/1`. Mutationsergebnisse sind
asynchron (`202`) und enthalten `case_id`, `state` und `event_id`. Validierung,
Rechte, Zustandsübergänge und Auditpfad sind dieselben Domain-Aufrufe wie in der
Bedienoberfläche.

## Events und Webhooks

Events tragen `schema_version: operator-event/1`, eine stabile `event_id`, eine
stabile `aggregate_id`, `community_id`, `event_type`, `occurred_at` und
`payload`. In v1 dürfen optionale Felder ergänzt werden. Bestehende Felder,
Bedeutungen oder Typen werden erst in einer neuen Hauptversion geändert.

OpenLEG sendet die kanonischen JSON-Bytes per POST. `OpenLEG-Delivery` bleibt
bei Wiederholungen stabil. `OpenLEG-Signature` ist
`sha256=` plus dem hexadezimalen HMAC-SHA-256 über den unveränderten Body. Der
signierte Body enthält zusätzlich `delivery_id` mit demselben Wert wie der
Header und `sent_at` als UTC-Zeitstempel im ISO-8601-Format. Bei jedem Versuch
aktualisiert OpenLEG `sent_at`; `delivery_id` und `event_id` bleiben stabil.

Der Empfänger prüft zuerst die Signatur über die unveränderten Body-Bytes mit
dem Signing-Secret, bevor er JSON parst. Danach lehnt er Zustellungen ab, deren
signiertes `sent_at` mehr als fünf Minuten vor oder nach seiner aktuellen
Uhrzeit liegt. Fehlende oder ungültige Zeitstempel lehnt er ebenfalls ab.
Er dedupliziert anhand der signierten `event_id` und/oder `delivery_id` und
speichert die verarbeiteten IDs dauerhaft, in derselben Transaktion wie die
ausgelöste fachliche Änderung. So gilt ein Ereignis nach einem fehlgeschlagenen
Verarbeitungsversuch nicht als erledigt. Lassen sich externe Nebenwirkungen
nicht in diese Transaktion einbinden, hält der Empfänger einen
wiederaufnehmbaren Verarbeitungszustand und antwortet bis zum Abschluss nicht
mit 2xx; OpenLEG stellt die Zustellung dann erneut zu. Jeder externe Aufruf
erhält einen Idempotenzschlüssel aus der signierten `event_id`, oder der
Empfänger klärt ein unklares Ergebnis vor dem nächsten Versuch. So wiederholt
eine erneute Zustellung keine unumkehrbare Handlung wie eine Zahlung. Die
Wiederholungen sind begrenzt: Nach dem fünften fehlgeschlagenen Versuch bleibt
die Zustellung auf `failed`, und nur eine Freigabe durch die Administration über
`POST …/deliveries/{delivery_id}/retry` stellt sie erneut zu. Der unsignierte Header
`OpenLEG-Delivery` eignet sich nicht als vertrauenswürdiger Deduplizierungsschlüssel.
Ein Python-Beispiel steht in
[operator-operations-api.md](operator-operations-api.md#webhook-payloads-and-signature-verification).

Ein Cronlauf beansprucht atomar höchstens 50 Zustellungen. Andere Worker können
diese Zustellungen während der 15-minütigen Claim-Frist nicht übernehmen.
Fehler werden mit exponentiellem Abstand höchstens fünfmal versucht. Danach
bleibt die Delivery sichtbar im Status `failed`; ein erfolgreicher 2xx-Aufruf
endet im Status `delivered`.

Administrationen sehen Zustellversuche unter
`/leg/community/{community_id}/operator-api/deliveries`. Einen endgültig
fehlgeschlagenen Versuch können sie über
`POST …/deliveries/{delivery_id}/retry` kontrolliert erneut freigeben. Hat die
Zustellung ihre fünf Versuche aufgebraucht, erhält sie genau einen weiteren
Versuch. Wurde sie vorher abgebrochen, etwa wegen eines fehlenden
Webhook-Geheimnisses, erhält sie die verbleibenden Versuche bis fünf. Die
bisherigen Versuche bleiben gezählt.

## Handelnde Person oder Anwendung

Bei der Ausstellung wählen Sie `actor_kind`: `human` (Mensch), `service`
(Dienst) oder `agent` (Agent). `agent_label` ist ein optionaler Name mit
höchstens 200 Zeichen. Bestehende Credentials erhalten `service`.
Agentenanfragen für Gründungsanmeldungen und Mitgliedermutationen erhalten
HTTP 202 mit `state: awaiting_confirmation`, `request_id`,
`payload_fingerprint` und `confirmation_url`. Erst eine Administration der LEG
bestätigt die Anfrage im Dashboard. Bis dahin entstehen keine VNB-Vorgänge,
Pakete oder Zustellungen. Unbestätigte Anfragen laufen nach 72 Stunden ab.
Vorschauen mit `dry_run=true` sind auch für Agenten erlaubt und schreiben keine
Geschäftsdaten. Ungültige oder wiederholte `dry_run`-Werte sowie `dry_run` im
JSON-Body erhalten zuerst HTTP 400 mit `invalid_request`.

`created_by` bleibt die Gebäudeidentität für die Berechtigungsprüfung.
API-Schreibvorgänge speichern zusätzlich einen unveränderlichen `actor`:
`initiating_client_id`, `actor_kind` und `agent_label`. Er steht im
VNB-Vorgang, in Rechnungsantworten und ihren Ereignissen, in
Zahlungs-Lebenszyklusereignissen sowie in Messdatenläufen und deren
transaktionalen Operator-Ereignissen. Rotation, Widerruf und Namensänderungen
ändern diese Historie nicht. Alte Vorgänge ohne belegte Zuordnung bleiben
ohne `actor`; OpenLEG leitet ihn nicht nachträglich aus heutigen Credentials ab.

`operator-event/1` ergänzt nur das optionale Feld `actor`. Die
`client_id` der Webhook-Zustellung bezeichnet weiterhin den Empfänger.
Sie ist nicht die auslösende Client-ID. OpenLEG sendet `actor` mit auslösender
Client-ID, Akteursart und Agentenbezeichnung an alle Webhook-Empfänger der
LEG, deren Capability das Ereignis abdeckt. Eine Agentenbezeichnung enthält
deshalb keine vertraulichen Angaben.

## OAuth für Agenten

MCP-Hosts autorisieren sich über den in [mcp-sidecar.md](mcp-sidecar.md)
beschriebenen OAuth-Ablauf. Nur eine bestätigte Administration darf Rechte für
ihre LEG delegieren. Der Host fordert Capabilities an. Die Administration wählt eine LEG und
bestätigt die Rechte. Der ausgestellte Scope enthält genau diese LEG und
die bestätigten Capabilities. Die Zustimmung zeigt jede Capability in Deutsch oder Französisch.

Der MCP-Zugriffstoken gilt fünf Minuten und wird nie an die Betreiber-API
weitergegeben. Ein separates Credential pro Grant trägt `actor_kind=agent`.
Bestehende `olk_`-Credentials für Dienste funktionieren unverändert.
Die Antwort enthält ein rotierendes Refresh-Token. Unter der bestehenden
Credentials-Adresse sehen Sie OAuth-Freigaben mit LEG, Client, Berechtigungen,
Erstellungszeit und letzter Verwendung. «Agentenzugriff widerrufen» sperrt
die Freigabe und ihr separates Credential. Die nächste MCP-Anfrage und der
nächste Refresh werden abgewiesen. Die Aktion erfordert Ihre bestätigte
Administrationssitzung und ein CSRF-Token.
