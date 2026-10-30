# Operator operations API v1

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

Collections accept `status`, `limit` (1–100), and an opaque `cursor`; responses
contain `items` and `next_cursor`. Replaying a mutation with the same
`Idempotency-Key` and the same payload returns the stored result with
`replayed: true`; reusing a key with a different payload is a `409` conflict. Operational projections exclude source
evidence, invoice snapshots, participant identifiers, attachments, messages,
and bank-account data.

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
Errors have `error` and `schema_version`; authentication, capability, scope,
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
Idempotency-Key: mutation-2026-10-01-building-7
Content-Type: application/json

{"mutation_id":"member-2026-1","participant_id":"building-7","mutation_type":"join","effective_date":"2026-10-01","source_agreement_id":"agreement-v3"}
```

## Rate limits

Every credential carries an hourly request budget (`rate_limit_per_hour`).
The HTTP boundary rejects a request once the budget is spent with
`429` and `Retry-After: 3600`; the window resets one hour after the first
counted request. Revoking or rotating a credential takes effect immediately on
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
