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

```sh
pip install -r requirements-mcp.txt
OPENLEG_MCP_ENABLED=true OPENLEG_OPERATOR_ORIGIN=http://localhost:5000 python -m mcp_sidecar
```

The standalone listener binds to `127.0.0.1:8000` by default.
`OPENLEG_MCP_BIND_HOST` overrides the bind address. The Compose overlay sets
it to `0.0.0.0` inside the container so Caddy can connect; only Caddy publishes
a host port, bound to loopback. Host and Origin checks do not restrict which
network clients can reach the listener.

The process refuses to start unless `OPENLEG_MCP_ENABLED=true`. The configured
origin must contain only a scheme, host and optional port. Requests use the
fixed API paths, ignore proxy environment variables and never follow redirects. Do not give this process
database environment variables or the Flask `.env` file.

For local Compose development, start the normal application first, then:

```sh
OPENLEG_MCP_ENABLED=true docker compose -f docker-compose.yml -f compose.mcp.yml --profile mcp up --build mcp mcp-proxy
```

This adds a loopback-only Caddy endpoint at `http://localhost:8081/mcp`.
Neither service nor its proxy route exists in the default Compose configuration.
Stop the two optional services to remove the endpoint.

Connect MCP Inspector with Streamable HTTP to that URL (or
`http://localhost:8000/mcp` for the Python command). Set its HTTP Authorization
header to `Bearer ` followed by your existing `olk_` credential. Initialize,
list tools, then call `list_billing_periods` with your `community_id` and
optionally `limit`, `cursor` or `status`. Supply the header on every request;
there is no stored session credential. Keep tokens out of tool arguments.

This is a development tracer, not production agent access. It has no database
access or OAuth. OAuth replacement is tracked in
#847; production enablement belongs in `openleg-ops` after the readiness gate.

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
listed but require their bearer on each call; anonymous calls fail with the
JSON-RPC authorization error described in
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
`{"code": "rate_limited", "retry_after": 60}`. The delay is the remaining
window rounded up to whole seconds, with the same shape as an upstream 429.
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
