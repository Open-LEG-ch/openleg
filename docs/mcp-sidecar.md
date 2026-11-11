# Local MCP sidecar

The separate `mcp_sidecar` process exposes exactly one tool,
`list_billing_periods`, over Streamable HTTP at `/mcp`. Its input schema comes
from `listPeriods` in `docs/openapi/operator-v1.yaml`. It declares `billing.read`
and `readOnlyHint`, and returns the operator API's `items` and `next_cursor`.
Cross-community calls return the tool error `not_found`.

Install `requirements-mcp.txt` in a separate Python 3.11 environment. For tests,
`requirements-dev.txt` includes it; Flask's runtime requirements do not.

```sh
pip install -r requirements-mcp.txt
OPENLEG_MCP_ENABLED=true OPENLEG_OPERATOR_ORIGIN=http://localhost:5000 python -m mcp_sidecar
```

The process refuses to start unless `OPENLEG_MCP_ENABLED=true`. The configured
origin must contain only a scheme, host and optional port. Requests use the
fixed operator API path and never follow redirects. Do not give this process
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
access, writes, OAuth, or additional tools. OAuth replacement is tracked in
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
continues to select the upstream operator API.

The SDK rejects an unlisted Host with 421 and an unlisted Origin with 403.
A missing Origin is accepted for non-browser clients, as supported by the SDK;
Host validation still applies. The literal Origin `null` is rejected.
