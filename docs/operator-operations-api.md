# Operator operations API v1

All endpoints use `Authorization: Bearer …`, are scoped by the community in the
path, and return `schema_version: operator-api/1`. A credential needs the
matching `*.read` or `*.mutate` capability. Cross-community access returns 404.

Collections are available below
`/api/operator/v1/communities/{community_id}`:

- `GET /metering/jobs` and `/metering/calculated-deliveries`
- `GET /billing/periods`, `/billing/invoices`, and `/billing/cases`
- `GET /payments/matches`

Collections accept `status`, `limit` (1–100), and an opaque `cursor`; responses
contain `items` and `next_cursor`. Operational projections exclude source
evidence, invoice snapshots, participant identifiers, attachments, messages,
and bank-account data.

Mutations require an `Idempotency-Key` of 1–128 characters:

- `POST /metering/jobs/{id}/retry` retries only a failed job belonging to the
  community's tenant.
- `POST /billing/cases/{id}/responses` accepts `message` and the next permitted
  `status` (`acknowledged` or `resolved`).
- `POST /payments/matches/{id}/confirm` accepts `invoice_id` and confirms only
  an exact CHF payment against a delivered invoice in the same community.

There is deliberately no invoice update endpoint. Invoice totals and snapshots
remain immutable, and payment confirmation uses the existing invoice lifecycle.
Errors have `error` and `schema_version`; authentication, capability, scope,
validation, conflict, and rate errors use 401, 403, 404, 400/409, and 429.

Lifecycle events use `operator-event/1`. Webhook requests include
`OpenLEG-Delivery` and an `OpenLEG-Signature: sha256=…` HMAC over the exact body.
Event payloads contain only lifecycle status, error code, or period bounds.
