#!/usr/bin/bash
# Installs the package into the active environment and exercises the
# console scripts against a fixture database, to catch packaging/import
# regressions that unit-level checks might miss. Assumes `pip` on PATH
# points at the environment to test. viatom-o2ring-sync-files needs a real
# BLE connection to do anything beyond --version/--help, so it isn't
# exercised further here.
set -e

WORKDIR="$(mktemp -d)"
trap 'rm -rf "${WORKDIR}"' EXIT

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "==> Installing package from ${REPO_DIR}"
pip install --quiet "${REPO_DIR}"

echo "==> Creating fixture database and config"
python3 "${REPO_DIR}/scripts/make-fixture-db.py" "${WORKDIR}/readings.db"

cat > "${WORKDIR}/config.ini" <<EOF
[monitor]
address = AA:BB:CC:DD:EE:FF

[storage]
db_path = ${WORKDIR}/readings.db

[daemon]
log_level = INFO
EOF

echo "==> viatom-o2ring-daemon"
viatom-o2ring-daemon --version
viatom-o2ring-daemon --help > /dev/null
viatom-o2ring-daemon --config "${WORKDIR}/config.ini" --check-config

echo "==> viatom-o2ring-sync-files"
viatom-o2ring-sync-files --version
viatom-o2ring-sync-files --help > /dev/null

echo "==> viatom-o2ring-report"
viatom-o2ring-report --version
viatom-o2ring-report --help > /dev/null
viatom-o2ring-report --config "${WORKDIR}/config.ini" --output "${WORKDIR}/out.pdf"
test -s "${WORKDIR}/out.pdf"
viatom-o2ring-report --config "${WORKDIR}/config.ini" --format csv --output "${WORKDIR}/out.csv"
grep -q "Date/Time" "${WORKDIR}/out.csv"

echo "==> viatom-o2ring-prune"
viatom-o2ring-prune --version
viatom-o2ring-prune --help > /dev/null
viatom-o2ring-prune --config "${WORKDIR}/config.ini" --older-than 9999 | grep -q "Would delete 0"

echo "==> viatom-o2ring-alert-check"
viatom-o2ring-alert-check --version
viatom-o2ring-alert-check --help > /dev/null
viatom-o2ring-alert-check --config "${WORKDIR}/config.ini" | grep -q "disabled"

echo "==> viatom-o2ring-api"
viatom-o2ring-api --version
viatom-o2ring-api --help > /dev/null
viatom-o2ring-api --config "${WORKDIR}/config.ini" | grep -q "disabled"

echo "==> Smoke test passed"
