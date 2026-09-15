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

Formation and membership use the versioned VNB exchange contract:

- `POST /formation/submissions` prepares or sends the current signed formation
  package. Its canonical payload fingerprint makes replays idempotent.
- `POST /membership-mutations` accepts `mutation_id`, `participant_id`,
  `mutation_type`, `effective_date`, `source_agreement_id`, and an optional
  `after` object. The mutation ID is stable within one community.

These two mutations call the same domain seam as the dashboard. Manual and
automated delivery therefore share authorization, validation, state changes,
evidence, and events. Responses exclude package and acknowledgement bytes.

There is deliberately no invoice update endpoint. Invoice totals and snapshots
remain immutable, and payment confirmation uses the existing invoice lifecycle.
Errors have `error` and `schema_version`; authentication, capability, scope,
validation, conflict, and rate errors use 401, 403, 404, 400/409, and 429.

Lifecycle events use `operator-event/1`. Webhook requests include
`OpenLEG-Delivery` and an `OpenLEG-Signature: sha256=…` HMAC over the exact body.
Event payloads contain only lifecycle status, error code, or period bounds.
Formation event types start with `formation.submission.`. Membership event types
start with `membership.mutation.`. Event and aggregate identifiers remain stable
for a replay of the same transition. Consumers must ignore unknown payload keys
within version 1. Removing or changing a key requires a new schema version.

Example membership request:

```http
POST /api/operator/v1/communities/leg-1/membership-mutations
Authorization: Bearer olk_…
Content-Type: application/json

{"mutation_id":"member-2026-1","participant_id":"building-7","mutation_type":"join","effective_date":"2026-10-01","source_agreement_id":"agreement-v3","after":{"metering_point_id":"CH123"}}
```

Dashboard-session administrators manage credentials at
`/leg/community/{community_id}/operator-api/credentials` (create/list), with
`/{client_id}/rotate` and `/{client_id}/revoke`; mutation requests require the
dashboard CSRF token. They can inspect delivery attempts at
`/leg/community/{community_id}/operator-api/deliveries` and retry a failed
delivery at `/{delivery_id}/retry`. The cron worker dispatches pending events
through `POST /api/cron/process-operator-webhooks`, protected by `CRON_SECRET`.
