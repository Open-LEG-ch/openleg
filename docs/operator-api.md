# Private Betreiber-API v1

Die Betreiber-API bindet jedes Credential an genau eine LEG. Sie dient für
Gründungsanmeldungen und VNB-Mitgliedermutationen; sie ist kein Datenbankexport.

## Credentials

Eine bestätigte LEG-Administration erstellt, liest, rotiert und widerruft
Credentials unter `/leg/community/{community_id}/operator-api/credentials`.
Der API-Token und das Webhook-Signing-Secret werden nur bei der Erstellung
angezeigt. OpenLEG speichert vom API-Token ausschließlich den SHA-256-Hash. Das
Signing-Secret wird unabhängig davon aus dem Instanzschlüssel und der stabilen
Client-ID abgeleitet und bleibt bei einer Tokenrotation gleich. Rotation macht
den bisherigen Token sofort ungültig; Widerruf wird
bei der nächsten HTTP-Anfrage geprüft.

Capabilities sind explizit: `formation.read`, `formation.mutate`,
`membership.read` und `membership.mutate`. Ein Credential erhält keine
Capability automatisch. Pro Credential gilt standardmäßig ein Limit von 100
Anfragen pro Stunde.

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
Empfänger prüft die Signatur mit dem Signing-Secret und verarbeitet jede
Delivery-ID höchstens einmal.

Ein Cronlauf beansprucht atomar höchstens 50 Zustellungen. Andere Worker können
diese Zustellungen während der 15-minütigen Claim-Frist nicht übernehmen.
Fehler werden mit exponentiellem Abstand höchstens fünfmal versucht. Danach
bleibt die Delivery sichtbar im Status `failed`; ein erfolgreicher 2xx-Aufruf
endet im Status `delivered`.

Administrationen sehen Zustellversuche unter
`/leg/community/{community_id}/operator-api/deliveries`. Einen endgültig
fehlgeschlagenen Versuch können sie über
`POST …/deliveries/{delivery_id}/retry` kontrolliert erneut freigeben.
