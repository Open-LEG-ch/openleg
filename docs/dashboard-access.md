# Dashboard access

OpenLEG protects resident and LEG dashboards with short-lived, one-time links.
The URL carries a random token, never a building ID. The database stores only
its SHA-256 hash.

## Link lifecycle

- A person requests a link at `/dashboard` with the email address attached to
  the profile. The response stays generic, whether a matching profile exists or
  not.
- Interactive requests use `DASHBOARD_ACCESS_TOKEN_TTL_SECONDS`. The default is
  900 seconds. OpenLEG clamps configured values to a safe range.
- Automated lifecycle email links use a separate 24 hours validity period.
- Opening a valid link consumes it atomically and redirects to a clean
  `/dashboard` URL. Reusing, revoking or opening an expired link fails closed.
- Logging out revokes remaining unused links for that building.

## Session and form protection

After a successful exchange, OpenLEG keeps the building ID in a signed server
session cookie. The cookie is HttpOnly and SameSite=Lax. It is Secure by default
when `APP_BASE_URL` uses HTTPS. Dashboard mutations use the session identity and
require a CSRF token. Query parameters and submitted building IDs cannot replace
that identity.

Private dashboard and document responses send `Cache-Control: no-store` and a
`no-referrer` policy. Document downloads verify community membership before
returning content.

## Create a community

Signed-in founders open `/leg/community/create` from their resident dashboard.
The form asks for the community name and distribution model. A successful POST
creates the founder's administrator membership and redirects to the LEG dashboard.
Validation errors keep the form available for correction. Creating a record does
not constitute VNB approval or activate billing.

A private community view includes the signed-in member's own membership even
when that person has not opted into neighbour discovery. Every other household
still requires current discovery consent. The displayed member list and counts
use the same visible rows; revocation removes the other household on the next
read. A person outside the community cannot use its identifier to open the view.

## Local verification

Open `/dashboard/demo` for the resident flow and `/leg/dashboard/demo` for the
operator flow. Both routes use synthetic examples. No production data is needed
or displayed.

Run the related tests with:

```bash
pytest tests/test_dashboard_access.py tests/test_dashboard_access_routes.py -q
```

Use the full repository gate before a pull request:

```bash
scripts/tdd_cycle.sh gate
```
