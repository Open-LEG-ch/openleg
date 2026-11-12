# Operator operations API v1

The authoritative machine-readable contract for MCP and CLI consumers is
[operator-v1.yaml](openapi/operator-v1.yaml). Route coverage and PostgreSQL-backed
HTTP response tests check it against the running operator blueprint.

All endpoints use `Authorization: Bearer …`, are scoped by the community in the
path, and return `schema_version: operator-api/1`. A credential needs the
matching `*.read` or `*.mutate` capability. Cross-community access returns 404.

Collections are available below
`/api/operator/v1/communities/{community_id}`:

- `GET /formation` returns formation submission cases.
- `GET /membership-mutations` returns participant mutation cases.
- `GET /metering/jobs` and `/metering/calculated-deliveries`
- `GET /billing/periods`, `/billing/invoices`, and `/billing/cases`
- `GET /payments/matches`

Metering, billing, and payment collections accept `status`, `limit` (1–100),
and an opaque `cursor`; responses contain `items` and `next_cursor`.

Replay works in two ways. The metering retry, invoice case response and
payment confirmation read `Idempotency-Key`: replaying the same key with the
same payload returns the stored result with `replayed: true`, and reusing a
key with a different payload is a `409` conflict. Formation submissions and
membership mutations ignore that header: a formation submission deduplicates
by its canonical payload fingerprint, and a membership mutation by its
`mutation_id`. Retry a membership mutation with the same `mutation_id`; a new
`mutation_id` creates a new mutation.

Read items expose only the fields listed below. Fields absent from a store row
are omitted; null values remain null. New store columns are not exposed
automatically.

| Read resource | Item fields |
| --- | --- |
| `GET /formation` (`submissions`) | `case_id`, `community_id`, `adapter_key`, `contract_version`, `state`, `next_action`, `response_status`, `retryable`, `failure_code`, `created_at`, `delivered_at`, `acknowledged_at` |
| `GET /membership-mutations` (`mutations`) | `case_id`, `mutation_id`, `community_id`, `mutation_type`, `effective_date`, `source_agreement_id`, `state`, `next_action`, `response_status`, `created_at`, `updated_at` |
| `GET /metering/jobs` | `id`, `territory`, `started_at`, `finished_at`, `status`, `attempts`, `downloaded_files`, `imported_files`, `imported_readings`, `error_code` |
| `GET /metering/calculated-deliveries` | `id`, `contract_version`, `format_version`, `transport`, `community_id`, `period_start`, `period_end`, `status`, `diagnostics`, `record_count`, `received_at` |
| `GET /billing/periods` | `id`, `community_id`, `period_start`, `period_end`, `total_production_kwh`, `total_allocated_kwh`, `total_surplus_kwh`, `total_network_discount_chf`, `status` |
| `GET /billing/invoices` | `id`, `invoice_number`, `gross_chf`, `issue_date`, `due_date`, `lifecycle_state` |
| `GET /billing/cases` | `id`, `invoice_id`, `category`, `status`, `created_at`, `updated_at` |
| `GET /payments/matches` | `id`, `invoice_id`, `entry_reference`, `booking_date`, `amount`, `currency`, `payment_reference`, `is_reversal`, `match_decision` |

Formation and membership reads return `submissions` and `mutations`,
respectively, without pagination fields. Operators correlate membership reads
using `mutation_id`; `participant_id` is not returned. Source evidence,
invoice snapshots, participant records, attachments, and messages are excluded.
Payment metadata includes amount, currency, booking date, and entry/payment
references. No read projection returns bank-account details: IBAN, account
holder, account number, debtor name, or debtor address.

Dashboard reads use the administrator's session at
`/leg/community/{community_id}/operator-api`:

| Read resource | Item fields |
| --- | --- |
| `GET /credentials` (`credentials`) | `id`, `community_id`, `name`, `capabilities`, `rate_limit_per_hour`, `active`, `created_at`, `updated_at`, `last_used_at`, `webhook_url`, `actor_kind`, `agent_label`, `webhook_requires_rotation` |
| `GET /deliveries` (`deliveries`) | `delivery_id`, `status`, `attempt_count`, `response_status`, `next_attempt_at`, `last_attempt_at`, `event_id`, `event_type`, `aggregate_id`, `occurred_at` |

Credential listing also requires the dashboard CSRF token.
`webhook_requires_rotation` is derived from the presence of a webhook URL
without a stored signing secret. Credential lists never return tokens, token
hashes, or signing secrets.

Mutations require an `Idempotency-Key` of 1–128 characters:

- `POST /metering/jobs/{id}/retry` retries only a failed job belonging to the
  community's tenant.
- `POST /billing/cases/{id}/responses` accepts `message` and the next permitted
  `status` (`acknowledged` or `resolved`).
- `POST /payments/matches/{id}/confirm` accepts `invoice_id` and confirms only
  an exact CHF payment against a delivered invoice in the same community.

Formation and membership use the versioned VNB exchange contract:

- `POST /formation/submissions` prepares or sends the current signed formation
  package. Its canonical payload fingerprint makes replays idempotent.
- `POST /membership-mutations` accepts `mutation_id`, `participant_id`,
  `mutation_type`, `effective_date`, and `source_agreement_id`. The mutation ID
  is stable within one community. `join` and `exit` derive their facts from the
  LEG record; `change` additionally accepts an `after` object whose keys are
  limited to `status`, `role`, and `access_roles`. Unknown keys are rejected.

These two mutations call the same domain seam as the dashboard. Manual and
automated delivery therefore share authorization, validation, state changes,
evidence, and events. Responses exclude package and acknowledgement bytes.

There is deliberately no invoice update endpoint. Invoice totals and snapshots
remain immutable, and payment confirmation uses the existing invoice lifecycle.
Errors retain generic `error` text and `schema_version`, and carry a closed-enum
`code`, optional `field`, and boolean `retryable`. Domain exception messages stay
internal. The enum in the OpenAPI contract is authoritative; clients must branch
on codes, never on error text. Authentication, capability, scope,
validation, conflict, and rate errors use 401, 403, 404, 400/409, and 429.

Lifecycle events use `operator-event/1`. Webhook requests include
`OpenLEG-Delivery` and an `OpenLEG-Signature: sha256=…` HMAC over the exact body.
Event payloads contain the minimum fields for the transition: lifecycle status,
error code, period bounds, or the case/mutation identifier of the aggregate —
never snapshot content, participant records, or bank data.
Formation event types start with `formation.submission.`. Membership event
types start with `membership.mutation.`. Metering event types start with
`metering.`. Invoice lifecycle and case events start with `invoice.`. Payment
events start with `payment.`. Event and aggregate identifiers remain stable
for a replay of the same transition. Consumers must ignore unknown payload keys
within version 1. Removing or changing a key requires a new schema version.
Both the dashboard and this API drive the same store layer, so a lifecycle
change from either surface emits the same signed event exactly once.

Example membership request:

```http
POST /api/operator/v1/communities/leg-1/membership-mutations
Authorization: Bearer olk_…
Content-Type: application/json

{"mutation_id":"member-2026-1","participant_id":"building-7","mutation_type":"join","effective_date":"2026-10-01","source_agreement_id":"agreement-v3"}
```

## Rate limits

Every credential carries an hourly request budget (`rate_limit_per_hour`).
The HTTP boundary rejects a request once the budget is spent with
`429`, `Retry-After: 3600` and the operator error body (`error`,
`schema_version`); the window resets one hour after the first counted request.
The application-wide per-address limit can also answer `429`, with
`Retry-After: 60` and a body of `error` and `retry_after_seconds`; the
contract describes both bodies. Revoking or rotating a credential takes effect immediately on
the next request: revoked credentials answer `401`, rotated ones invalidate the
previous token.

## Credential handling

The token and the webhook secret are returned once at creation (and rotation)
and are never stored in plain text or repeated in list responses — only a
SHA-256 hash is kept. Store them in the client's secret manager at creation
time. Every API response is sent with `Cache-Control: no-store`.

## Webhook payloads and signature verification

A delivery posts the canonical JSON body with these headers:

```http
OpenLEG-Delivery: <delivery-id>
OpenLEG-Signature: sha256=<hex hmac>
Content-Type: application/json
```

The signature is an HMAC-SHA256 over the exact raw body bytes, keyed with the
per-client webhook secret disclosed at credential creation. Verify before
parsing:

```python
import hashlib, hmac


def verify(body: bytes, secret: str, header_value: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header_value)
```

A minimal `membership.mutation.acknowledged` payload:

```json
{
  "schema_version": "operator-event/1",
  "event_id": "<stable event id>",
  "event_type": "membership.mutation.acknowledged",
  "aggregate_id": "<case id>",
  "community_id": "leg-1",
  "occurred_at": "<iso timestamp>",
  "payload": {"case_id": "<case id>", "state": "acknowledged"}
}
```

Consumers must verify the signature, treat unknown payload keys as ignorable
within version 1, and respond to non-2xx delivery outcomes by waiting for the
bounded retry schedule rather than re-posting events themselves.

Dashboard-session administrators manage credentials at
`/leg/community/{community_id}/operator-api/credentials` (create/list), with
`/{client_id}/rotate` and `/{client_id}/revoke`; mutation requests require the
dashboard CSRF token. They can inspect delivery attempts at
`/leg/community/{community_id}/operator-api/deliveries` and retry a failed
delivery at `/{delivery_id}/retry`. The cron worker dispatches pending events
through `POST /api/cron/process-operator-webhooks`, protected by `CRON_SECRET`.

## MCP reads

The opt-in sidecar generates one read-only tool for each GET operation in the
OpenAPI contract. Names use snake_case operation IDs; the existing
`list_billing_periods` name remains the alias for `listPeriods`.
Tool metadata records the operation ID and its required capability. Descriptions
and input schemas come from the contract. Formation and membership reads have
no pagination or status filter. Formation returns at most the latest 20 cases;
membership returns at most the latest 100. Older cases cannot be fetched through
these reads.

Read `openleg://communities/{community_id}/summary` through the advertised
resource template. Its sections are:

- `formation`: the state of the latest submission by `created_at`, or
  `no submission`; requires `formation.read`.
- `open_invoice_cases`: cases with status `open` or `acknowledged`;
  requires `cases.read`.
- `latest_billing_period`: the period with the latest `period_end`, or null;
  requires `billing.read`.

The sidecar follows all collection pages before computing a section. A denied
or failed source, including a failure on a later page, returns
`available: false` and a `reason` for that section. It never returns a partial
section as complete. Resource reads use the caller's credential on every
upstream request and consume its normal API request budget. Data fields come
only from the read projections above.

Tool results preserve external text as data and add `untrusted_fields`, a list
of JSON pointers identifying values that must not be followed as instructions.
This marker is present in both structured and textual MCP content:

- Formation `response_status` and `failure_code` can come from VNB adapter
  responses. Membership `response_status` can come from the same source.
- `next_action` currently comes from adapter/domain decisions; both case
  collections conservatively mark it untrusted as well.
- Calculated-delivery `diagnostics` contains validation output about VNB input.
  The entire value is conservatively marked, including nested text.
- Calculated-delivery `contract_version`, `format_version` and `transport` are
  taken from the VNB delivery and kept when the delivery is rejected, so they
  are marked untrusted too.

Null or absent values need no marker. States, IDs, timestamps, amounts and
counts are structured domain data, not VNB instructions. Case messages,
response evidence and package bytes are excluded by the operator projection.
The summary copies no VNB response text: it uses formation state and the
billing/case projections.

## MCP error boundary

Tool failures expose `code`, `field` (null when absent), and `retryable` in
`structuredContent`. Only contract-declared codes and known field names pass
through; upstream error text is discarded. HTTP 503 remains a retryable tool
failure; a failed HTTP connection is a JSON-RPC internal error (-32603).

Missing, malformed, or rejected bearer credentials produce a JSON-RPC
authorization error (-32011, application-defined; chosen to avoid codes MCP SDKs already use, such as -32001 for a request timeout), with the stable reason in
`error.data`. They are never sent as `CallToolResult`. The pinned MCP Python
SDK catches exceptions inside its `call_tool` decorator, so the boundary promotes
these failures to `McpError` outside that decorator. The SDK has no dedicated
authorization exception for an upstream credential rejection. This development
bearer flow does not implement the OAuth HTTP challenge planned in #838.

## VNB previews

Both VNB write endpoints accept the query option `dry_run=true` (default
`false`). The option must appear at most once, with the exact value `true`
or `false`. Repeated values, other values and a `dry_run` key in the JSON
body return 400 with `invalid_request` before domain validation. A successful preview returns HTTP 200 with `schema_version`,
`intended_action` (`prepare_manual_package` or `send_through_adapter`),
`payload_fingerprint` and `recipient`. The recipient comes from the adapter's
`ExchangeContract.recipient_description`; absent descriptions return
`manual / not configured`.

Previews run the same authorization and preflight validation as submission.
They check existing conflicts without claiming or changing cases, including
retryable cases. They build no stored package and call no transport. Cases,
events, webhooks and idempotency records remain unchanged; credential usage and
last-used accounting still apply. A preview is a point-in-time check, not a
reservation or a prediction of delivery success.

The development MCP sidecar exposes `preview_formation_submission` and
`preview_membership_mutation`. They require `formation.mutate` and
`membership.mutate`, respectively, and always request a dry run. Their inputs
come from the operator contract; callers cannot disable the dry run.

Agent credentials may preview both VNB-bound writes with `dry_run=true`.
Real submissions (option absent or `false`) return 403 `capability_denied`.
Malformed, repeated, or body-supplied `dry_run` options return 400
`invalid_request` before the actor denial or any domain call.

## Invoice case previews and MCP

`POST /billing/cases/{id}/responses?dry_run=true` validates the message and
current status transition without changing cases, messages, events, webhook
deliveries or the idempotency ledger. The response contains `id`,
`previous_status`, the proposed `status`, `dry_run: true` and `schema_version`.
Usage accounting still applies. A preview does not reserve the transition;
the real call validates the current state again.

`dry_run` accepts one query value, exactly `true` or `false`. Repeated values,
other spellings and a `dry_run` body key return `400 invalid_request`.
Omitting the option or passing `false` performs the write and requires an
`Idempotency-Key`. HTTP previews do not require a key.

The MCP tool `respond_invoice_case` maps to `respondInvoiceCase` and requires
`cases.mutate`. Its arguments are `community_id`, `case_id`, `message`,
`status`, `idempotency_key` and optional boolean `dry_run`. The tool requires
a key even for previews so the same arguments can be used for the real call.
Only `acknowledged` and `resolved` are supported. The tool is marked as a
state-changing, destructive, idempotent operation. It returns no message
history. Records and events retain the initiating credential's actor snapshot.
