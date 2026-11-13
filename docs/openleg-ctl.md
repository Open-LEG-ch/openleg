# openleg-ctl

`openleg-ctl` reads the operator API and previews every write that supports a
dry run. It cannot submit business writes. `scripts/openleg` remains the self-host lifecycle tool.

Install with Python 3.11 or later:

```sh
python -m pip install .
```

The CLI uses the standard library HTTP client, PyYAML and jsonschema. It does not
install or import the MCP SDK. The wheel includes the authoritative operator
contract from `docs/openapi/operator-v1.yaml`.

Set `OPENLEG_URL` to the server origin (for example `https://openleg.example`).
Supply `OPENLEG_TOKEN` through your secret manager or shell environment. There
is no token flag or keyring integration; keyring is not a project dependency.
Use HTTPS outside local development. Redirects and ambient HTTP proxies are
not followed. Each request has a 15-second socket timeout; there are no retries.

```sh
openleg-ctl --help
openleg-ctl listPeriods --community-id leg-1 --limit 10 --json
openleg-ctl listPeriods --community-id leg-1 --cursor 42 --json
openleg-ctl submitFormation-preview --community-id leg-1
openleg-ctl submitMembershipMutation-preview --community-id leg-1 \
  --mutation-id change-1 --participant-id building-7 --mutation-type change \
  --effective-date 2026-12-01 --source-agreement-id agreement-1 \
  --after '{"role":"member"}' --json
openleg-ctl respondInvoiceCase-preview --community-id leg-1 --case-id 42 \
  --message "Checked." --status acknowledged
```

Read command names are the contract's exact `operationId`, including its
spelling and capitalisation. Operations supporting `dry_run` get only an
`operationId-preview` command. These commands always send `dry_run=true`;
there is no override. Use each command's `--help` for its capability and inputs.
Input flags are lowercase and replace underscores with hyphens. Header inputs
such as `Idempotency-Key` become flags like `--idempotency-key` and are sent
as request headers. Object and array inputs use JSON.

Every command accepts `--url` to override `OPENLEG_URL` and `--json` for compact
machine-readable output. Without `--json`, output is indented JSON for reading
in a terminal. Collections return one page and its `next_cursor`; pass that
cursor unchanged on the next invocation.

Success exits 0. API, configuration and network failures exit 1 and print a
JSON object on stderr containing `code`. Argument errors exit 2 and print
`invalid_arguments` without repeating supplied values. API codes come from
the contract; unrecognised responses use `operator_error`. `network_error`
means no usable HTTP response arrived. A 429 includes `retry_after` in seconds.
Tokens are redacted from output, and upstream error messages are discarded.

Previews need the corresponding mutate capability. They validate current
state without storing packages, cases, events, deliveries or idempotency
records. Credential usage accounting still applies. A preview reserves nothing.
