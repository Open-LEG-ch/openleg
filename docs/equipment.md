# Private equipment inventory

A verified owner opens `/dashboard/equipment` from the owner dashboard.
Inventory does not require a LEG membership, an enrolled telemetry installation
or neighbour-sharing consent. A community administrator or telemetry viewer
cannot access another owner's equipment.

The forms record manufacturer/model, firmware, logger, AC kW and optional
PV DC kWp, cloud dependency and installer access. Empty facts and unknown
states remain unknown. Owner reports, device observations and owner-entered
installer evidence carry separate source kinds and review dates. OpenLEG does
not verify these claims. A known source needs a real, non-future review date.

Each hardening task records applicability for the exact manufacturer/model,
action state and its own evidence. Marking a task done requires an applicable
function and dated evidence. MFA support is a manual model/account review;
OpenLEG does not infer it from a brand. Changing an existing manufacturer,
model or firmware resets the checklist, manual cloud review and backup
assertions to unknown, including evidence. Review them after saving the new
context. There is no aggregate security score or backup-ready badge.

Local readings do not disable vendor-cloud control. The manual vendor-cloud
review and owner-declared backup capability remain separate from telemetry
and from installer evidence. Firmware, cloud disconnection, network rules,
grid-protection settings and electrical changes remain tasks for the
responsible installer/operator. This feature performs no scans or commands.

## Owner API

All routes require the same verified `dashboard_building_id` session as the
owner dashboard. Mutations require its `X-CSRF-Token` header. A missing session
returns 401, an unverified/deleted owner 403, and another owner's UUID 404.
Every response, including errors, is private with `Cache-Control: no-store`
and `Referrer-Policy: no-referrer`.

| Method | Path | Result |
| --- | --- | --- |
| GET | `/api/equipment/v1/records` | Owner's records |
| POST | `/api/equipment/v1/records` | Create; 201 with `record` |
| GET | `/api/equipment/v1/records/<uuid>` | One private record |
| PUT | `/api/equipment/v1/records/<uuid>` | Replace the record |
| DELETE | `/api/equipment/v1/records/<uuid>` | Delete including checklist/evidence |
| POST | `/api/equipment/v1/records/<uuid>/handoff` | Attachment `equipment-handoff.json` |

Create and replace accept only uncompressed JSON, bounded to 24,576 bytes.
There are at most 100 records per owner; an additional create returns 409.
Unknown fields, invalid enums, non-finite/negative power values or invalid
evidence return 400. Empty JSON creates an entirely unknown record. PUT
replaces the entire record, so omitted facts become unknown. Power values
are positive, at most 100,000, with at most three decimal places, and return
as decimal strings.

Example body, with no serial or private network data:

```json
{
  "manufacturer": "Example",
  "model": "Roof 1",
  "firmware": "1.2",
  "rated_ac_kw": "8.25",
  "pv_dc_kwp": "10.2",
  "evidence": {
    "kind": "device_observation",
    "reviewed_on": "2026-01-02"
  },
  "checklist": {
    "account_mfa": {
      "applicability": "unknown",
      "status": "open",
      "evidence": {
        "kind": "owner_report",
        "reviewed_on": "2026-01-02"
      }
    }
  }
}
```

Evidence kinds are `unknown`, `owner_report`, `device_observation` and
`installer_evidence`. Unknown evidence has no date/link. Optional `source_url`
values must be public HTTPS documentation URLs without credentials, query
parameters, fragments, IP literals or local/internal hostnames. OpenLEG never
fetches these links.

Checklist keys are `default_passwords`, `account_mfa`, `obsolete_access`,
`update_support`, `exposed_services` and `network_separation`. Each accepts
`applicability` (`unknown`, `applicable`, `not_supported`, `not_applicable`),
`status` (`unknown`, `open`, `done`, `needs_installer`) and `evidence`.
Known applicability requires a recorded manufacturer and model.

`vendor_cloud_review` has `status` (`unknown`, `not_reviewed`, `reviewed`),
`remote_access` (`unknown`, `possible`, `disabled`, `none`) and `evidence`.
Only a reviewed cloud entry can have dated evidence or a known result.
`backup` has `capability` (`unknown`, `none`, `partial`, `available`,
`requires_modification`) and independent `installer_evidence`; its source
kind can only be unknown or installer evidence.

## Handoff and deletion

The owner explicitly downloads stored device facts, task statuses, dates and
source links. No automatic message is sent. The allowlisted download omits
profile/contact data and the private fields `serial_number`, `network_details`,
`installer_users` and `private_notes`. The owner should review device names and
public source links before forwarding the file. Their downloaded copies remain
outside application deletion.

Deleting an equipment record deletes all evidence in the same JSON row.
Deleting the registration cascades to every owned record. No equipment or
checklist history survives in a separate audit table. See the
[data catalog](data-catalog.md) and [retention matrix](retention-matrix.md).

The forms link to [BACS device security](https://www.bacs.admin.ch/en/device-security)
and [Swissolar backup guidance](https://www.swissolar.ch/de/wissen/anlagenbetrieb/solarstrom-bei-netzausfall).
These general sources do not establish the capabilities of an owner's model.
