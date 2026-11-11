# Billing storage risk with PostgreSQL

Evidence for #861, within #855, measured on 2026-11-10 at
`c1474ba9746f5670db6a74590f7416d7f58ea18b` from `origin/main`.
Only this report changes the repository.

## Reproduction

Use a clean checkout of that revision. Install `requirements-dev.txt` in an
external Python 3.11 venv, including its pinned Ruff and coverage versions.
The run used Python 3.11.16, pytest 9.2.0, Ruff 0.17.4, coverage.py 7.17.2,
psycopg2-binary 2.9.13, Babel 2.18.0 and PostgreSQL 16.15
(`16.15-0ubuntu0.24.04.1`). The producer is `crap-analysis/run_python.py`,
evidence schema v2, with `python-ast-mccabe-v1` complexity tool version 1.

Docker was unavailable. PostgreSQL packages were downloaded and extracted
without installing system services:

```bash
mkdir -p /tmp/openleg-861-pg
cd /tmp/openleg-861-pg
apt download postgresql-16 postgresql-client-16 libpq5
for package in *.deb; do dpkg-deb -x "$package" root; done
export LD_LIBRARY_PATH=/tmp/openleg-861-pg/root/usr/lib/x86_64-linux-gnu
pgbin=/tmp/openleg-861-pg/root/usr/lib/postgresql/16/bin
"$pgbin/initdb" -D /tmp/openleg-861-pg/utf8 -A trust -U postgres \
  --no-locale --encoding=UTF8
"$pgbin/pg_ctl" -D /tmp/openleg-861-pg/utf8 \
  -l /tmp/openleg-861-pg/server-utf8.log \
  -o '-h 127.0.0.1 -p 5861 -k /tmp/openleg-861-pg -c fsync=off -c synchronous_commit=off' start
```

From the repository, the final producer command was:

```bash
python3.11 -m venv /tmp/openleg-861-py311
/tmp/openleg-861-py311/bin/pip install -r requirements-dev.txt
export PATH=/tmp/openleg-861-py311/bin:$PATH
export DATABASE_URL=postgresql://postgres@127.0.0.1:5861/postgres
skill="$CRAP_ANALYSIS_SKILL_DIR/scripts"  # local crap-analysis skill checkout
python "$skill/run_python.py" --repo . --scope store \
  --run-id "$(git rev-parse HEAD)" \
  --output-dir /tmp/openleg-861-utf8-evidence \
  --test-command 'python -m pytest -q -ra --junitxml=/tmp/openleg-861-utf8-tests.xml' \
  --boundary gt --threshold 30 --max-age-seconds 3600 \
  > /tmp/openleg-861-utf8-producer.log 2>&1
```

The same command with `DATABASE_URL=''`, output directory
`/tmp/openleg-861-py311-without-pg`, XML file
`/tmp/openleg-861-py311-without-pg-tests.xml` and scorer output
`/tmp/openleg-861-py311-without-pg-producer.log` supplies the comparison.
Native reports, coverage data/JSON, complexity JSON and hashed test logs remain
in those external directories. Dependency versions are also preserved in
`/tmp/openleg-861-py311-versions.txt`. No generated evidence was edited.

Disk-sync durability was disabled only on this disposable cluster. These runs
do not establish crash durability. Two discarded attempts exposed setup errors:
Python 3.12.3 failed translation extraction, and an ASCII cluster rejected
`Dättwil`. The final run uses the project's Python 3.11 and explicit UTF-8.
Neither failure was bypassed or used for scoring.

## Results and remaining risk

The full PostgreSQL run passed 5,162 tests, skipped 192 and had zero failures
or errors. `test_store_billing_postgres.py` passed all 8 cases,
`test_payment_acceptance_postgres.py` passed all 25, and
`test_tdd_cycle_script.py` passed all 6, each with zero skips.
The remaining skips were 179 sandbox/local-binary cases and 13 Redis cases.
The sandbox binary environment was not configured; those skips include retained
invoice identity tests. This is not complete PostgreSQL or billing coverage.

The same-commit comparison passed 4,451 tests and skipped 903. Thus 711 cases
ran when `DATABASE_URL` was set. The historical 218 skips in #861 describe an
earlier run, not this revision.

Both producer outputs report valid evidence and exit 1 for risk, not test
failure. Of 398 store functions, 6 exceed the strict `> 30` boundary with
PostgreSQL, versus 26 without it. None of the 39 billing functions exceeds 30.
The highest billing scores follow, rounded from native scorer output.
CRAP is `complexity² × (1 - line coverage/100)³ + complexity`, using function-range
line coverage, not branch coverage or evidence of strong assertions.

| Billing function | Complexity | Line coverage | CRAP |
| --- | ---: | ---: | ---: |
| `approve_billing_period` | 23 | 93.44% | 23.15 |
| `prepare_invoice_delivery` | 13 | 73.33% | 16.20 |
| `save_billing_period` | 15 | 100.00% | 15.00 |
| `_policy_dict` | 5 | 27.27% | 14.62 |
| `correct_invoice` | 11 | 71.88% | 13.69 |
| `reconcile_bank_statement` | 11 | 88.89% | 11.17 |
| `complete_invoice_delivery` | 8 | 65.00% | 10.74 |
| `confirm_invoice_delivery` | 8 | 65.00% | 10.74 |
| `get_community_for_building` | 3 | 11.11% | 9.32 |

`get_community_for_building` is untested at the body level in both runs: only
its definition executes. Its 11.11% is not evidence that a query ran.
`_policy_dict` exercises only dictionary input; string decoding and rejection
remain untested. Delivery and correction have tests, but their missing lines
include invalid-state refusals, conflicting corrections, and exception handling.
Their gaps persist with PostgreSQL enabled. Low aggregate scores do not make
these money and audit operations safe.

Approval was partly hidden by skipped tests: coverage rises from 75.41% to
93.44%, and CRAP falls from 30.87 to 23.15. Draft saving reaches 100% from
92.86%, sequence allocation reaches 100% from 62.50%, and reviewed payment
confirmation reaches 100% from 96%. Bank-entry listing rises from 11.11% to
66.67%; community-period and invoice-event listing each rise from 12.50% to
62.50%. These functions had coverage suppressed by skips, rather than wholly
missing tests. Payment acceptance also checks concurrent review, replay,
lifecycle rechecks after lock waits, and rollback when notification writes fail.

For #855, use [this evidence](research-billing-storage-risk.md) to scope a
follow-up around invoice delivery reservation and completion, with refusal and
rollback tests plus mutation checks. Approval remains the most complex operation,
but its former threshold breach largely reflected skipped PostgreSQL coverage.
Correction refusal paths are another bounded follow-up. These are prioritization
suggestions; this PR changes no billing behavior and does not complete #855.
