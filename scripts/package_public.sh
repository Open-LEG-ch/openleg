#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${1:-${TMPDIR:-/tmp}/openleg-public}"
ARCHIVE="${2:-}"

if [[ -z "${DEST}" || "${DEST}" == "/" || "${DEST}" == "${ROOT_DIR}" || "${DEST}" == "${ROOT_DIR}/"* ]]; then
  echo "Destination must be outside the source repository: ${DEST}" >&2
  exit 1
fi

rm -rf "${DEST}"
mkdir -p "${DEST}"

copy_file() {
  local path="$1"
  [[ -f "${ROOT_DIR}/${path}" ]] || return 0
  mkdir -p "${DEST}/$(dirname "${path}")"
  cp "${ROOT_DIR}/${path}" "${DEST}/${path}"
}

copy_as() {
  local source="$1"
  local target="$2"
  mkdir -p "${DEST}/$(dirname "${target}")"
  cp "${ROOT_DIR}/${source}" "${DEST}/${target}"
}

copy_tracked_tree() {
  local path="$1"
  while IFS= read -r file; do
    copy_file "${file}"
  done < <(git -C "${ROOT_DIR}" ls-files "${path}")
}

sanitize_snapshot_files() {
  python3 "${ROOT_DIR}/scripts/sanitize_public_snapshot.py" \
    "${DEST}/app.py" \
    "${DEST}/database.py" \
    "${DEST}/email_automation.py"
}

# Slice 1: public project metadata and local examples.
for path in \
  .dockerignore .env.example .eslintrc.json .gitignore \
  CONTRIBUTING.md DEPLOYMENT.md LICENSE README.md SECURITY.md \
  Caddyfile Dockerfile Procfile deploy.example.sh docker-compose.yml \
  pyproject.toml railway.toml requirements-dev.txt requirements.txt
do
  copy_file "${path}"
done

# Slice 2: public application runtime.
for path in \
  api_public.py app.py billing_engine.py cache.py data_enricher.py database.py \
  deepsign_integration.py document_generator.py email_automation.py email_utils.py \
  formation_wizard.py generate_images.py health.py meter_data.py ml_models.py \
  municipality.py passenger_wsgi.py public_data.py security_utils.py \
  stripe_integration.py tenant.py token_persistence.py utility_portal.py
do
  copy_file "${path}"
done
copy_tracked_tree static
copy_tracked_tree templates
rm -f "${DEST}/templates/admin/pipeline.html"
rm -f "${DEST}/templates/admin/strategy.html"
rm -f "${DEST}/templates/emails/municipality_outreach.html"

# Slice 3: public docs, CI policy, and tests.
for path in \
  docs/repo-boundary.md \
  .github/dependabot.yml \
  .github/forbidden-paths.txt
do
  copy_file "${path}"
done
copy_tracked_tree .github/ISSUE_TEMPLATE
copy_tracked_tree .github/workflows
copy_tracked_tree tests
rm -f "${DEST}/tests/test_agents_md.py"
rm -f "${DEST}/tests/test_admin_pipeline.py"
rm -f "${DEST}/tests/test_admin_strategy.py"
rm -f "${DEST}/tests/test_demand_signal.py"
rm -f "${DEST}/tests/test_docs_boundary_contract.py"
rm -f "${DEST}/tests/test_e2e_integration.py"
rm -f "${DEST}/tests/test_lea_reports.py"
rm -f "${DEST}/tests/test_municipality_outreach.py"
rm -f "${DEST}/tests/test_municipality_targeting.py"
rm -f "${DEST}/tests/test_private_content_absent.py"
rm -f "${DEST}/tests/test_ralph_loop.py"
rm -f "${DEST}/tests/test_sales_pipeline.py"
copy_file scripts/__init__.py
copy_file scripts/tdd_cycle.sh

sanitize_snapshot_files

ruff format "${DEST}" >/dev/null

blocked_paths="$(
  cd "${DEST}"
  find . -type f | sed 's#^\./##' | grep -E \
    '(^|/)(archive|overnight|prd|tmp|output|grants|outreach|strategy|private|internal|workspace|node_modules|__pycache__|\.orch|\.claude)(/|$)|(^|/)(handoff|server\.log|.*\.(db|sqlite|sqlite3|pem|key|crt))$|(^|/)(research[^/]*\.md|docs/research\.md|docs/.*strategy.*\.md|docs/.*internal.*\.md)$' \
    || true
)"
if [[ -n "${blocked_paths}" ]]; then
  echo "Blocked paths found in public package:" >&2
  echo "${blocked_paths}" >&2
  exit 1
fi

blocked_content="$(
  grep -RInE \
    '83\.228\.[0-9]+\.[0-9]+|BEGIN (RSA |OPENSSH |EC )?PRIVATE KEY|ssh[[:space:]]+-i[[:space:]]+|w[g]usta|b[a]denleg|/U[s]ers/|sk-a[n]t-|Güney[[:space:]]+Usta|sihliconvalley' \
    --exclude-dir=tests \
    --exclude=package_public.sh \
    "${DEST}" \
    || true
)"
if [[ -n "${blocked_content}" ]]; then
  echo "Blocked content found in public package:" >&2
  echo "${blocked_content}" >&2
  exit 1
fi

(
  cd "${DEST}"
  find . -type f | sed 's#^\./##' | sort > PUBLIC-MANIFEST.txt
)

if [[ -n "${ARCHIVE}" ]]; then
  mkdir -p "$(dirname "${ARCHIVE}")"
  tar -C "$(dirname "${DEST}")" -czf "${ARCHIVE}" "$(basename "${DEST}")"
fi

echo "Public package ready: ${DEST}"
if [[ -n "${ARCHIVE}" ]]; then
  echo "Archive ready: ${ARCHIVE}"
fi
