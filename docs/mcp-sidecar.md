# Local MCP sidecar

The separate `mcp_sidecar` process exposes tools over Streamable HTTP at `/mcp`.
The input schema for `list_billing_periods` comes
from `listPeriods` in `docs/openapi/operator-v1.yaml`. It declares `billing.read`
and `readOnlyHint`, and returns the operator API's `items` and `next_cursor`.
Cross-community calls return the tool error `not_found`.

`respond_invoice_case` maps to `respondInvoiceCase` with `cases.mutate`.
Supply `community_id`, `case_id`, `message`, `status` (`acknowledged` or
`resolved`) and `idempotency_key`. Set `dry_run: true` to validate without
business writes. Reuse that key with the same payload for the real call;
replays return `replayed: true`. This tool has `readOnlyHint: false`,
`destructiveHint: true` and `idempotentHint: true`. It returns transition
metadata, without case messages. See [the operator contract](operator-operations-api.md#invoice-case-previews-and-mcp).

Install `requirements-mcp.txt` in a separate Python 3.11 environment. For tests,
`requirements-dev.txt` includes it; Flask's runtime requirements do not.

For a static-token local tracer, run Flask on port 5000, then:

```sh
pip install -r requirements-mcp.txt
OPENLEG_MCP_ENABLED=true OPENLEG_MCP_DEV_STATIC=true OPENLEG_OPERATOR_ORIGIN=http://127.0.0.1:5000 python -m mcp_sidecar
```

The standalone listener binds to `127.0.0.1:8000` in dev-static mode and
`0.0.0.0:8000` in OAuth mode.
`OPENLEG_MCP_BIND_HOST` overrides the bind address; in dev-static mode the
process refuses any address other than `127.0.0.1`, `::1` or `localhost`.
The Compose overlay runs in OAuth mode and sets
it to `0.0.0.0` inside the container so Caddy can connect; only Caddy publishes
a host port, bound to loopback. Host and Origin checks do not restrict which
network clients can reach the listener.

The process refuses to start unless `OPENLEG_MCP_ENABLED=true`. The configured
origin must contain only a scheme, host and optional port. Requests use the
fixed API paths, ignore proxy environment variables and never follow redirects. Do not give this process
database environment variables or the Flask `.env` file.
Connect to `http://127.0.0.1:8000/mcp` with an `Authorization: Bearer olk_…`
header on every request. Static mode requires loopback for both processes.

Compose uses OAuth. Register the exact callback URL of your MCP host and
create an introspection secret, then start Flask with the overlay as well:

```sh
export OPENLEG_MCP_ENABLED=true
export OPENLEG_OAUTH_INTROSPECTION_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
export OPENLEG_OAUTH_DOWNSTREAM_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"
export OPENLEG_OAUTH_CLIENTS='{"host":{"name":"Local MCP host","redirect_uris":["http://localhost:6274/oauth/callback"]}}'
docker compose -f docker-compose.yml -f compose.mcp.yml --profile mcp up --build flask mcp mcp-proxy
```

Configure your host with client ID `host` and its registered redirect URI.
Connect to `http://localhost:8081/mcp`. Sign into the dashboard at
`http://localhost:8081` before approving consent. Caddy routes OAuth discovery,
authorization and resource metadata through that origin. The sidecar shares
Flask's network namespace so its internal HTTP connection stays on loopback;
it receives no database credentials. Keep the introspection secret stable
across restarts of these services. Stop the optional services to remove MCP.

Production enablement remains in `openleg-ops` after the #838 readiness gate.

## Host and Origin validation

DNS-rebinding protection is always enabled. Configure comma-separated allow-lists
with `OPENLEG_MCP_ALLOWED_HOSTS` and `OPENLEG_MCP_ALLOWED_ORIGINS`. Whitespace
around entries is ignored; an empty list prevents startup. Setting either
variable replaces its defaults.

The default hosts are `localhost` and `127.0.0.1`, each without a port or with
port `8000` or `8081`. Default origins are `http://localhost` and
`http://127.0.0.1` with port `8000`, `8081` or `6274` (MCP Inspector).
The Compose overlay restricts hosts to port `8081` and origins to ports
`8081` and `6274`. Caddy forwards the client's Host header to the sidecar.

For a different proxy address or Inspector port, list its exact host and origin,
including the port. Origins include the scheme and have no trailing slash.
These settings describe incoming MCP requests; `OPENLEG_OPERATOR_ORIGIN`
selects the upstream app for both public and operator routes.

The SDK rejects an unlisted Host with 421 and an unlisted Origin with 403.
A missing Origin is accepted for non-browser clients, as supported by the SDK;
Host validation still applies. The literal Origin `null` is rejected.

## Anonymous public tools

Initialize and list tools without a credential. The following read-only tools
use operation IDs and input schemas from `docs/openapi/public-v1.json`:

| Tool | Public route |
| --- | --- |
| `get_municipalities` | `GET /api/v1/municipalities` |
| `get_municipalities_bfs` | `GET /api/v1/municipalities/{bfs}` |
| `get_municipalities_bfs_tariffs` | `GET /api/v1/municipalities/{bfs}/tariffs` |
| `get_municipalities_bfs_solar` | `GET /api/v1/municipalities/{bfs}/solar` |
| `get_tariffs` | `GET /api/v1/tariffs` |
| `get_search` | `GET /api/v1/search` |
| `get_rankings` | `GET /api/v1/rankings` |
| `get_site_rankings` | `GET /api/v1/site/rankings` |
| `get_site_rankings_movers` | `GET /api/v1/site/rankings/movers` |

For example, call `get_search` with `{"q": "Dietikon"}` or
`get_municipalities_bfs_tariffs` with `{"bfs": 243, "year": 2026}`.
Address endpoints, calculators, score breakdowns, LEG potential, registry,
homepage and template endpoints are not exposed.

Public calls never forward the caller's Authorization header. They return the
public API response unchanged, including decimal strings. Operator tools remain
listed. In OAuth mode, anonymous `tools/call` requests for operator tools and
`resources/read` requests return HTTP 401 with `WWW-Authenticate` discovery
metadata and scopes. Initialization, tool listing, resource-template listing and
public tool calls work without Authorization. An invalid Authorization header
returns the same 401 even for public calls. The endpoint inspects single and
batch JSON-RPC requests and replays their exact bytes to the SDK; bodies over
1 MiB return 413. Malformed JSON is handled by the SDK. Each operator tool
checks the token's community and its own required capability before calling
upstream with the separate downstream credential.

Dev-static mode retains the JSON-RPC authorization error described in
[the operator contract](operator-operations-api.md#mcp-error-boundary).

Calls use the same public HTTP routes and their existing rate limits. The app
counts requests from the sidecar's upstream IP, so callers share that budget.
A 429 returns the tool error `rate_limited` with structured content
`{"code": "rate_limited", "field": null, "retryable": true, "retry_after": 60}`.
`retry_after` appears only when the upstream supplies a numeric Retry-After
delay in seconds. A 400 returns `invalid_arguments`, a 404 `not_found`, other
statuses `public_error`, and an unreachable upstream `public_unavailable`.
Upstream error text is never returned.

## Per-client public budgets

The sidecar allows 60 public tool calls per client in a fixed 60-second window.
All nine public tools share that budget, including calls with invalid arguments
and calls carrying an operator credential. Initialization and tool discovery do
not consume it. Operator tools retain their upstream per-credential budgets.

| Environment variable | Default | Meaning |
| --- | --- | --- |
| `OPENLEG_MCP_PUBLIC_CALLS` | `60` | Calls allowed per window |
| `OPENLEG_MCP_PUBLIC_WINDOW_SECONDS` | `60` | Window length from the first call |
| `OPENLEG_MCP_PUBLIC_MAX_CLIENTS` | `10000` | Maximum stored client budgets |
| `OPENLEG_MCP_TRUSTED_PROXY` | empty | Exact IP address of one trusted proxy |

Numeric settings must be positive integers. Invalid settings prevent startup.
Limits live in memory per sidecar process: restarting resets them, and multiple
processes multiply the available budget. Clients sharing an IP share a budget.
Expired entries are removed on the next public call. When the table is full,
new clients receive a rate limit until the earliest entry expires; active
budgets are never evicted to admit new addresses.

A rejected call never reaches Flask. It returns `isError: true`, text
`rate_limited`, and structured content
`{"code": "rate_limited", "field": null, "retryable": true, "retry_after": 60}`.
The delay is the remaining window rounded up to whole seconds, with the same
shape as an upstream 429.
The app's shared upstream limits still apply: enough distinct clients can
exhaust them. This local limit does not reserve upstream capacity per client.

### One trusted Caddy hop

By default the key is the connecting peer in ASGI `scope["client"]`.
The entry point disables Uvicorn's automatic proxy-header processing. If you
launch Uvicorn yourself, use `--no-proxy-headers` to preserve that boundary.

To distinguish clients behind Caddy, set `OPENLEG_MCP_TRUSTED_PROXY` to
Caddy's exact connecting IP on the sidecar network. The local Compose overlay
passes this variable through. Find the proxy's address with
`docker inspect` after starting it; recreate the sidecar with the setting.
For a persistent setup, pin the proxy address in the deployment network
configuration and update the setting if that address changes. Do not use
a hostname, CIDR, wildcard, or the public address of the proxy.

`Caddyfile.mcp` overwrites `X-Real-IP` with
`{http.request.remote.host}`, the peer Caddy actually sees. Only when the
sidecar peer matches the configured proxy does the sidecar accept that header.
It requires exactly one valid IP value; absent, malformed, duplicated, or
comma-separated values fall back to the proxy's shared budget.
`Forwarded` and `X-Forwarded-For` are never used. These addresses are never
forwarded to Flask.

Without the trusted-proxy setting, the local Caddy users share Caddy's peer
budget. This setup supports one proxy hop; adding another proxy requires a
separate trust design. Production network configuration remains in
`openleg-ops`.

## OAuth für MCP-Hosts (#847)

OAuth ist der Standard. Die Flask-App stellt einmalige Autorisierungscodes
(60 Sekunden) und opake Zugriffstokens (300 Sekunden) aus. PKCE akzeptiert
ausschliesslich S256. Registrieren Sie öffentliche Clients vorab; dynamische
Clientregistrierung gehört nicht zu diesem Ticket. Redirect-URIs müssen exakt
übereinstimmen. HTTPS ist Pflicht ausser für lokale Loopback-Adressen.

Konfiguration der Flask-App:

- `OPENLEG_OAUTH_ISSUER=https://app.example` (Origin ohne abschliessenden Slash).
- `OPENLEG_OAUTH_RESOURCE=https://mcp.example/mcp`.
- `OPENLEG_OAUTH_CLIENTS={"host":{"name":"Mein MCP-Host","redirect_uris":["https://host.example/callback"]}}`.
- `OPENLEG_OAUTH_INTROSPECTION_SECRET`: separater zufälliger Schlüssel mit
  mindestens 32 Zeichen, identisch in App und Sidecar.

Konfiguration des Sidecars:

- `OPENLEG_MCP_ENABLED=true`.
- `OPENLEG_OAUTH_ISSUER` wie oben.
- `OPENLEG_MCP_RESOURCE` entspricht `OPENLEG_OAUTH_RESOURCE` der App.
- `OPENLEG_OAUTH_INTROSPECTION_SECRET` wie oben.
- `OPENLEG_OPERATOR_ORIGIN`: HTTPS-Origin der App; HTTP nur auf Loopback.
- Host- und Origin-Allowlisten wie oben. Keine Datenbankzugangsdaten.

Der Host fordert Betreiber-Capabilities an, beispielsweise `billing.read`.
Die Administration wählt auf der Zustimmungsseite eine ihrer bestätigten LEG
und die bewilligten Rechte. Optional wählt `community:<community_id>` eine LEG
vor. Der ausgestellte Scope enthält genau diese LEG und die bestätigten Rechte.
Der Host schickt diesen Scope, `client_id`, `redirect_uri`, `response_type=code`,
`state`, `code_challenge`, `code_challenge_method=S256` und `resource` an
`/oauth/authorize`. Die bestätigte LEG-Administration meldet sich zuerst im
Dashboard an, öffnet die Autorisierung erneut und bestätigt die angezeigten
Rechte. Deutsch, Französisch, Englisch und Italienisch verwenden die
Übersetzungskataloge.

Der Host tauscht den Code per Formular-POST an `/oauth/token`:
`grant_type=authorization_code`, `code`, `client_id`, `redirect_uri`,
`code_verifier` und dieselbe `resource`. Er verwendet das erhaltene Token als
Bearer für `/mcp`. Der Zugriff gilt ausschliesslich für diese Ressource und LEG.

Der Sidecar introspektiert bei jeder Anfrage mit MCP-Token über
`POST /oauth/introspect`.
Das gemeinsame Secret authentifiziert den Sidecar. Die Antwort enthält neben
Issuer, Audience, Ablauf, Scope und LEG ein separates Downstream-Credential.
Introspektion vermeidet Signaturschlüssel im Sidecar und prüft Widerruf sofort;
sie benötigt dafür pro Anfrage einen zusätzlichen HTTP-Aufruf. Das
Downstream-Credential wird mit HMAC-SHA-256 aus dem zufälligen MCP-Zugriffstoken
und einem separaten Serverschlüssel abgeleitet. So kann die App das Credential
bei authentifizierter Introspektion erneut ausgeben und nur Hashes speichern.
Das MCP-Token allein reicht nicht zur Berechnung des Betreiber-Credentials.
Nur die Flask-App erhält `OPENLEG_OAUTH_DOWNSTREAM_KEY`: 32 zufällige Bytes als
64 kleingeschriebene Hexzeichen, ohne Standardwert. Der Sidecar erhält diesen
Schlüssel nie. Die App verweigert den Start bei fehlendem, falsch formatiertem,
offensichtlich schwachem Schlüssel (weniger als zwölf verschiedene Hexzeichen)
oder Wiederverwendung des Introspektionsschlüssels. Die Prüfung ersetzt keine
zufällige Erzeugung. Ein Schlüsselwechsel sperrt die Introspektion bestehender
Grants; bereits ausgegebene Betreiber-Credentials gelten bis Ablauf oder Widerruf.
Der Betreiber-Client trägt `actor_kind=agent`
und exakt die bewilligten Capabilities. Das Credential gilt höchstens so lange
wie der Grant. Codeaustausch, Introspektion und Betreiberzugriff prüfen, dass
die zustimmende Person weiterhin bestätigte Administration dieser LEG ist.
Abgelaufene oder widerrufene Grants sind auch mit einem bereits erhaltenen
Downstream-Credential unbrauchbar. Fehlender Community-Status sperrt den Grant.

Metadaten: App `/.well-known/oauth-authorization-server`
([RFC 8414](https://www.rfc-editor.org/rfc/rfc8414.html)); Sidecar
`/.well-known/oauth-protected-resource` und
`/.well-known/oauth-protected-resource/mcp`
([RFC 9728](https://www.rfc-editor.org/rfc/rfc9728.html)).
401-Antworten nennen die Capabilities im `scope` des `WWW-Authenticate`-Headers.
Die Ressourcenmetadaten nennen sie in `scopes_supported`.
Die Ressourcenbindung folgt
[RFC 8707](https://www.rfc-editor.org/rfc/rfc8707.html).

Nur lokal kann `OPENLEG_MCP_DEV_STATIC=true` den bisherigen `olk_`-Pfad
aktivieren. Der Prozesseinstieg bindet dann nur an Loopback (`127.0.0.1`,
`::1` oder `localhost`) und startet mit einer anderen Adresse nicht;
auch der Betreiber-Origin muss Loopback sein. Diesen Modus niemals über einen
Produktionsproxy veröffentlichen. In Produktion bleibt die Variable ungesetzt.
Proxy-Logs dürfen keine Authorization-Header, Formularinhalte oder OAuth-
Callback-Querystrings aufzeichnen.

Die Implementierung nutzt Zufallswerte, SHA-256, HMAC und URL-Verarbeitung aus
der Python-Standardbibliothek; sie implementiert kein eigenes JWT-Format.
Der Codeaustausch liefert auch ein opakes Refresh-Token (`olr_`).
Der Host erneuert per Formular-POST an `/oauth/token` mit
`grant_type=refresh_token`, `refresh_token`, `client_id` und derselben
`resource`. Jede erfolgreiche Erneuerung ersetzt Zugriffs- und Refresh-Token
atomar; auch das vorherige Zugriffstoken wird sofort ungültig.
Der Scope bleibt unverändert. Ein optionaler `scope` muss dieselbe Menge von
Berechtigungen enthalten; auch engere Scopes werden abgewiesen.
Refresh-Tokens gelten ab Codeaustausch absolut 30 Tage. Rotation verlängert
diese Frist nicht. Danach ist eine neue Zustimmung nötig.

Die Rotation für öffentliche Clients folgt
[RFC 9700, Abschnitt 4.14](https://www.rfc-editor.org/rfc/rfc9700.html#section-4.14).
OpenLEG speichert nur SHA-256-Hashes und behält verbrauchte Hashes zur
Wiederverwendungserkennung. Ein erneut verwendetes Refresh-Token widerruft
die ganze Freigabe samt Downstream-Credential. Bei zwei gleichzeitigen
Refreshes kann höchstens einer erfolgreich sein; der zweite widerruft auch
die gerade ausgegebenen Tokens. Hosts müssen Refreshes deshalb serialisieren.
Ein verlorenes Refresh-Ergebnis erfordert neue Zustimmung statt Wiederholung.

Die bestätigte LEG-Administration sieht und widerruft Freigaben unter
`/leg/community/{community_id}/operator-api/credentials`. Die HTML-Ansicht
zeigt Client, LEG, Scope, Erstellungszeit und letzte erfolgreiche Introspektion
oder Erneuerung. Widerruf benötigt Sitzung und CSRF-Token; Introspektion und
Refresh prüfen ihn bei jeder Anfrage. OAuth bleibt bis zur vollständigen Bereitschaftsprüfung aus
#838 für Produktion deaktiviert.

Das Zustimmungsformular übernimmt nur die expliziten OAuth-Protokollfelder
aus der Autorisierungsanfrage. Query-Parameter steuern keine Checkboxen oder
LEG-Auswahl. Der POST akzeptiert nur Protokollfelder und Formular-Steuerelemente.
Die bewilligten Capabilities müssen im angefragten Scope liegen; nur eine
aktuell bestätigte Administration kann sie für ihre LEG delegieren.
