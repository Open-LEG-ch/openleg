# Contributor onboarding

## What OpenLEG is

OpenLEG is open source infrastructure for Swiss Local Electricity Communities, known as LEGs. It combines the public website, product application, and API used to found and operate a LEG. This repository owns that public product; production deployment belongs outside it.

Choose the same pathway that the README gives each stakeholder:

| You are | Start here | What you can do |
| --- | --- | --- |
| Owner or founder | `/dashboard` | Open the owner dashboard and organise a LEG. |
| LEG operator | `/leg/dashboard` | Manage members, contracts, metering, and billing. |
| Municipality | `/gemeinde/dashboard` | Open the municipality dashboard. |
| Developer or self-hoster | `/api/v1/docs` | Use the API or run your own instance. |

## Five minute setup

Clone the repository and enter it:

```console
$ git clone https://github.com/Open-LEG-ch/openleg.git
Cloning into 'openleg'...
$ cd openleg
```

Check the tools already on your machine:

```console
$ scripts/contribute doctor
Required to run the gate
OK Python: 3.12.10
OK pytest: importable
OK ruff executable: 0.16.6, matches pin
OK python3 -m ruff: 0.16.6, matches pin
Required to match CI
OK node: present
OK npm: present
OK mypy: present
Reported only
INFO .venv: absent
```

Create `.venv` and install the development dependencies:

```console
$ scripts/contribute setup
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
The current shell is unchanged. Activate the virtual environment now:
source .venv/bin/activate
```

Pip writes its package installation log between the two command lines and the activation reminder. `setup` cannot activate the virtual environment for your current shell, so activate it separately. A successful `source` command has no output.

```console
$ source .venv/bin/activate
$ scripts/contribute doctor
Required to run the gate
OK Python: 3.12.10
OK pytest: importable
OK ruff executable: 0.16.6, matches pin
OK python3 -m ruff: 0.16.6, matches pin
Required to match CI
OK node: present
OK npm: present
OK mypy: present
Reported only
INFO .venv: active
```

The version numbers can change when the repository updates its pins. The status text tells you whether your checkout is ready.

## Getting oriented

Both orientation commands read `CONTEXT.md` when you run them. Start with the tour:

```console
$ scripts/contribute tour
OpenLEG orientation
Entry points
- app.py: application factory and local development server
- wsgi.py: production WSGI entry point
- api_public.py: public JSON API
Named seams
- database.get_connection
- tenant.get_tenant_config
Store modules
- store/building: Building registrations, consent-gated building reads, dashboard building data
- store/cluster: Provisional cluster assignments and cluster metadata
- store/ranking: PV snapshots, the ten-year panel, Rangliste read models
- store/profile: Gemeindeprofil: ElCom tariffs, profiles, Sonnendach
- store/billing: LEG communities, billing periods, versioned billing policies, atomic invoice approval snapshots, append-only invoice lifecycle audit
- store/email_queue: Outbound mail queue
- store/utility: EVU/VNB utility clients
- store/metering: Messpunkte, 15-minute E66 readings, SDAT import ledger
- store/meter: Per-building meter readings from the upload path
- store/registry: LEG registry entries and verification
- store/tenant: White-label tenant configs
- store/token: Auth and claim tokens
- store/analytics: Event log and the aggregate counts the dashboards read
- store/consent: The consent record a resident gives and can revoke
- store/document: Generated LEG documents and their signing status
- store/ops: LEA job reports and operational snapshots
- store/access_token: Hashed, single-use magic-link tokens, dashboard and municipality
Tests
- tests/: pytest tests and contract tests
Gate
- scripts/test.sh gate
```

Ask the glossary for a domain term when a name is unfamiliar:

```console
$ scripts/contribute glossary LEG
Lokale Elektrizitätsgemeinschaft. Neighbours who share locally produced electricity over the public grid at a reduced network fee. The product's reason to exist.
```

Run `scripts/contribute glossary` without a term to list every term.

## The rules that are not negotiable

- Work only with public or synthetic fixtures. Do not access production or use citizen data. The [repository boundary](repo-boundary.md) defines what stays outside this public repository.
- Contributions use AGPL-3.0-or-later. The [contribution terms](../CONTRIBUTING.md) state this license.
- `main` accepts pull requests only. Never push to it directly. The [execution rules](codex-execution.md) describe the review path.

## The change loop

Small reproducible fixes can go straight to a focused PR with a regression
test. Discuss changes to allocation, reconciliation or rounding, VNB formats,
billing lifecycle, and schema or API contracts in an issue before implementation.
Report vulnerabilities through the [private security reporting process](../CONTRIBUTING.md#security-vulnerabilities).

1. Write one failing behavior test before implementation.
2. Run exactly that pytest node. `scripts/contribute test` defaults to the red phase.

   ```console
   $ scripts/contribute test tests/test_contributor_onboarding.py::test_onboarding_guide_is_linked_from_public_entry_points
   tests/test_contributor_onboarding.py F                                   [100%]
   FAILED tests/test_contributor_onboarding.py::test_onboarding_guide_is_linked_from_public_entry_points
   ============================== 1 failed in 0.02s ===============================
   ```

3. Implement the smallest change that makes the behavior pass.
4. Run the local gate. For database behavior, also execute the PostgreSQL tests below.

   ```console
   $ scripts/contribute gate
   ```

   `scripts/contribute gate` is an alias for `scripts/test.sh gate`, the
   canonical harness pinned by `tests/test_test_harness.py`. You will also see
   `scripts/tdd_cycle.sh gate` in `CONTRIBUTING.md` and `README.md`; that runs
   the same pytest and ruff checks but resolves ruff as `<python> -m ruff`
   rather than the executable. Either goes green on a healthy machine, and
   `doctor` reports both ruff sources for exactly this reason.

5. Stage explicit paths. Never use `git add -A`. Use the paths from your change. For this guide, the command is:

   ```console
   $ git add tests/test_contributor_onboarding.py docs/contributor-onboarding.md README.md CONTRIBUTING.md
   ```

   A successful `git add` prints nothing.

6. Check the staged paths against the public repository boundary.

   ```console
   $ scripts/contribute check --staged
   ```

   A successful check prints nothing.

7. Commit, push your branch, and open a pull request against `main`. Address review comments and CI failures with the same test-first loop. A maintainer merges the pull request after review and all required checks pass.

`scripts/contribute test` accepts exactly one pytest node and no pytest flags because `scripts/tdd_cycle.sh` has that contract. A flag exits nonzero with a clear error instead of being silently dropped:

```console
$ scripts/contribute test -k something
usage: scripts/contribute [-h]
                          {doctor,check,glossary,gate,test,setup,tour} ...
scripts/contribute: error: unrecognized arguments: -k
```

## CI runs more than the local gate

`scripts/test.sh gate` and `scripts/tdd_cycle.sh gate` run the full pytest
suite, ruff lint and ruff formatting checks. Without `DATABASE_URL`, the suite
skips PostgreSQL tests; a green local gate does not prove those queries work.

The `ci/test` job in `.github/workflows/deploy.yml` supplies PostgreSQL 16 and
`DATABASE_URL`, so those tests execute in CI. The `ci/lint` job also runs:

```bash
npm ci
npm run build:css
git diff --exit-code -- static/css/openleg.css
mypy --ignore-missing-imports --follow-imports=skip app.py database.py api_public.py formation_wizard.py
```

`doctor` reports `node`, `npm`, and `mypy` for this reason. Missing CI-only
tools produce warnings, not a failing doctor result. The separate `ci/security`
job runs gitleaks over Git history and checks the forbidden-path policy in
`.github/forbidden-paths.txt`. Neither local gate runs those security checks.
Use the current workflows as the source of truth.

## PostgreSQL validation

Use PostgreSQL 16 client and server tools installed locally. The example starts
a fresh cluster as your ordinary OS user, bound to loopback on port 55432 with
no production credentials or data. Choose an unused port if 55432 is occupied,
and change both port occurrences and the URL below. Never point tests at a
production or shared database, even if you expect a test to be read-only.

Activate `.venv` first. Run this block in Bash from the repository root. Set
`OPENLEG_PG_BIN` and `OPENLEG_PG_SHARE` beforehand if the tools are installed
outside the paths returned by PostgreSQL 16's `pg_config`.

```bash
(
    set -e
    OPENLEG_PG_BIN="${OPENLEG_PG_BIN:-$(pg_config --bindir)}"
    OPENLEG_PG_SHARE="${OPENLEG_PG_SHARE:-$(pg_config --sharedir)}"
    OPENLEG_PG_VERSION="$("$OPENLEG_PG_BIN/postgres" --version)"
    printf '%s\n' "$OPENLEG_PG_VERSION"
    case "$OPENLEG_PG_VERSION" in
        *"PostgreSQL) 16."*) ;;
        *) echo 'PostgreSQL 16 is required' >&2; exit 1 ;;
    esac
    OPENLEG_PG_ROOT="$(mktemp -d /tmp/openleg-pg.XXXXXX)"
    openleg_pg_cleanup() {
        if [ -f "$OPENLEG_PG_ROOT/data/postmaster.pid" ]; then
            "$OPENLEG_PG_BIN/pg_ctl" -D "$OPENLEG_PG_ROOT/data" -m fast -w stop || return
        fi
        rm -rf -- "$OPENLEG_PG_ROOT"
    }
    trap openleg_pg_cleanup EXIT
    printf '%s\n' 'openleg-test-only' > "$OPENLEG_PG_ROOT/password"
    "$OPENLEG_PG_BIN/initdb" -L "$OPENLEG_PG_SHARE" \
        -D "$OPENLEG_PG_ROOT/data" --username=openleg_test \
        --auth-local=trust --auth-host=scram-sha-256 \
        --pwfile="$OPENLEG_PG_ROOT/password"
    "$OPENLEG_PG_BIN/pg_ctl" -D "$OPENLEG_PG_ROOT/data" \
        -l "$OPENLEG_PG_ROOT/server.log" \
        -o "-h 127.0.0.1 -p 55432 -k $OPENLEG_PG_ROOT" -w start
    "$OPENLEG_PG_BIN/createdb" -h "$OPENLEG_PG_ROOT" -p 55432 \
        -U openleg_test openleg_test
    export DATABASE_URL='postgresql://openleg_test:openleg-test-only@127.0.0.1:55432/openleg_test'

    python -m pytest -v -ra \
        tests/test_interest_postgres.py \
        tests/test_store_billing_postgres.py \
        tests/test_store_metering_postgres.py \
        tests/test_store_ops_postgres.py
    scripts/tdd_cycle.sh gate
)
```

Check the selected tests' individual results for `PASSED` and the summary for
zero skips. `SKIPPED` means the database behavior was not validated; `-ra`
prints the skip reason. A connection or privilege error must fail the run.
Add any other PostgreSQL test files relevant to your change.

The test login must connect to both the configured database and the maintenance
database `postgres`, create temporary databases (`CREATEDB`), create schema
objects, and drop its temporary databases. Cleanup may terminate connections to
those databases. The example creates a superuser inside this disposable cluster
only; do not grant these privileges on a production server.

The fixtures create randomly named databases and drop them in `finally` blocks.
The subshell's exit trap stops the server and removes the disposable cluster,
including databases left by an interrupted test, when stopping succeeds. If
`pg_ctl ... stop` fails, the directory is retained. Stop the server before
removing that directory. `DATABASE_URL` and the helper variables stay inside
the subshell. If the shell is killed before
the trap runs, stop that cluster with `pg_ctl -D <temporary-directory>/data -m fast -w stop`
before removing its temporary directory. Do not reuse a failed cluster as a
fresh validation result.

## Troubleshooting

Match the line from `doctor` exactly, then apply the fix.

| Exact diagnostic | What to do |
| --- | --- |
| `FAIL Python: 3.9, need 3.11 or newer` | Install Python 3.11 or newer, then rerun `scripts/contribute doctor`. |
| `FAIL pytest: not found. Next: python3 -m pip install -r requirements-dev.txt` | Run `scripts/contribute setup`, activate `.venv`, then rerun `doctor`. |
| `FAIL ruff executable: required 0.16.6, found 0.15.20. Next: python3 -m pip install -r requirements-dev.txt` | Reinstall the development requirements in the active environment. |
| `FAIL ruff sources: ruff executable 0.15.20; python3 -m ruff 0.16.6; versions disagree. Next: python3 -m pip install -r requirements-dev.txt` | Remove the stale executable from `PATH`, reinstall the development requirements, and rerun `doctor`. |
