# Tenant savings overrides: decision draft

Status: proposed, owner approval pending for [#553](https://github.com/Open-LEG-ch/openleg/issues/553).
This record prepares the decision; it does not authorize an override. No staging
change or legal approval is recorded here.

Evidence revision: `cdb8655c3bb85db7110e55f36c982beb20c345dc`.

## Implemented behavior

`formation_wizard.DEFAULT_SOLAR_KWH_PER_KWP` is 950. The tenant's
`white_label_configs.config.solar_kwh_per_kwp` overrides that assumption through
`tenant._merge_tenant_row`. Its unit is annual kWh per installed kWp. The value
changes an estimate shown to residents; it does not change metered energy or
an issued invoice.

The following routes pass the resolved tenant assumption to
`formation_wizard.calculate_savings_estimate`:

| Surface | Evidence |
| --- | --- |
| Resident calculator and dashboard | `POST /api/calculate_savings`, `app.py`, `templates/dashboard.html` |
| Formation estimate | `POST /api/formation/financial-model`, `app.py` |
| Public financial projection | `POST /api/v1/leg/financial-model`, `api_public.py` |

The dashboard displays the assumption returned by the calculator. Existing
regressions in `tests/test_dashboard_demo.py` and `tests/test_api_public.py`
exercise a synthetic value of 875. That fixture is not an approved operating
value or the boundary of an allowed range.

`tenant.invalidate_cache(territory)` clears the local process cache and the
Redis entry. Both cache TTLs are 300 seconds. A change procedure must account
for every worker's in-memory cache; clearing one process does not establish
that another worker has refreshed.

## Decision the owner must complete

| Required field | Approval record |
| --- | --- |
| Accountable policy owner, name and role | Pending |
| Who may propose a change | Pending |
| Independent approver, name and role | Pending |
| Who may apply an approved change | Pending |
| Allowed numeric range, unit and treatment of the default | Pending |
| Required evidence, including source, geography, period and assumptions | Pending |
| Review cadence and expiry or renewal rule | Pending |
| Audit location and access controls | Pending |
| Rollback trigger, responsible person and verification window | Pending |
| Decision date, approved revision and deployment scope | Pending |

The code's default and its comment about regional production do not ratify an
allowed range. A role in the code repository does not establish policy or legal
authority. Keep supporting documents that contain household data outside the
public repository; record a safe reference to approved evidence instead.

## Proposed change record

After ratification, each applied override should identify the territory,
previous and approved values, proposal and approval references, named applier,
application time and deployed code revision. Record the effective cache-refresh
time, verification results and any rollback. The owner must approve this record
format along with the policy.

The current JSON configuration path does not enforce the pending governance
fields. File implementation follow-ups for any controls required by the
ratified decision; do not describe a written procedure as a runtime check.

## Staging evidence to collect after approval

Use a named, authorized staging tenant and the approved test value. Record the
revision and the prior tenant configuration before applying it through the
authorized operations workflow. A deployment or release uses the
`openleg-deploy` skill; this document grants no deployment authority.

1. Capture the baseline assumption and estimates on every surface above using
   the same consumption, PV capacity and community-size inputs where applicable.
2. Apply the approved test override. Wait for or explicitly refresh all tenant
   caches using the approved procedure.
3. Repeat every request and inspect the dashboard's displayed assumption.
   Confirm that each response exposes the approved value in `assumptions`.
   Keep different projection horizons distinct when comparing estimates.
4. Restore the prior configuration, refresh or wait for all caches, and repeat
   the baseline checks. Record the outcome and any mismatch as a follow-up.

Evidence still pending: authorized staging tenant, approved value, named
applier and approver, baseline and changed responses, restoration results.
Local synthetic tests cannot supply those approvals or staging results.
