# Read-only release validation

`OPENLEG_RELEASE_READ_ONLY` is a process setting. Unset, an empty value or `0`
keeps normal operation. `1` enables validation mode. Any other nonempty value
fails application startup, including when database checks are disabled.

Validation mode serves only GET/HEAD for the fixed public release-proof routes,
municipality profiles, health checks and static assets. Unknown routes, private
routes, token links, cron, operator/admin handlers and other methods return 503
with `Retry-After: 60` and `Cache-Control: no-store` before their handlers run.
Other public APIs and installer endpoints also remain unavailable in this mode.

Startup opens the PostgreSQL pool but skips schema initialization and default
tenant seeding. The owning deployment workflow must initialize the exact
candidate separately in an explicitly writable process before validation.
Every application checkout starts read-only transactions, including newly grown
pools and forked workers, so regular writes through `database.get_connection`
fail at PostgreSQL while reads remain available. Shared-store CLI and background
work use the same control.

Activate by restarting the same verified candidate with the flag unset or `0`.
Changing a Flask configuration value cannot activate the process. The deployment
workflow owns backups, validation, activation and recovery; this setting does
not replace those gates. After new application state is written, freeze the
current candidate for a validated roll-forward correction rather than starting
an older billing image that ignores the new evidence lifecycle.

This is an operating control. It does not restrict privileged direct database
connections, deliberately changed transaction settings, external writers, Redis
or remote services. The operator must quiesce independent producers and preserve
the corresponding recovery plan.
