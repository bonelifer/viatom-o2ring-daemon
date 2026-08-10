#!/usr/bin/bash
# Installs viatom-o2ring-daemon: creates a venv, installs the package from
# this checkout, seeds the config, creates the service user, and installs
# and enables the systemd unit. Re-running is safe: it skips steps that are
# already done (existing config, existing user) and upgrades the rest.
set -e

if [[ "${EUID}" -ne 0 ]]; then
    echo "This script must be run as root (e.g. with sudo)." >&2
    exit 1
fi

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    echo "Usage: sudo ./install.sh"
    echo "Installs viatom-o2ring-daemon as a systemd service. No options."
    exit 0
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/opt/viatom-o2ring-daemon"
CONFIG_DIR="/etc/viatom-o2ring-daemon"
SERVICE_USER="viatom-o2ring-daemon"

echo "==> Creating virtual environment at ${INSTALL_DIR}/venv"
python3 -m venv "${INSTALL_DIR}/venv"

echo "==> Installing viatom-o2ring-daemon from ${REPO_DIR}"
"${INSTALL_DIR}/venv/bin/pip" install --quiet --upgrade pip
"${INSTALL_DIR}/venv/bin/pip" install --quiet "${REPO_DIR}"

echo "==> Linking commands into /usr/bin"
ln -sf "${INSTALL_DIR}/venv/bin/viatom-o2ring-daemon" /usr/bin/viatom-o2ring-daemon
ln -sf "${INSTALL_DIR}/venv/bin/viatom-o2ring-sync-files" /usr/bin/viatom-o2ring-sync-files
ln -sf "${INSTALL_DIR}/venv/bin/viatom-o2ring-report" /usr/bin/viatom-o2ring-report
ln -sf "${INSTALL_DIR}/venv/bin/viatom-o2ring-prune" /usr/bin/viatom-o2ring-prune
ln -sf "${INSTALL_DIR}/venv/bin/viatom-o2ring-alert-check" /usr/bin/viatom-o2ring-alert-check
ln -sf "${INSTALL_DIR}/venv/bin/viatom-o2ring-api" /usr/bin/viatom-o2ring-api
cp "${REPO_DIR}/scripts/generate-scheduled-report.sh" "${INSTALL_DIR}/generate-scheduled-report.sh"
chmod +x "${INSTALL_DIR}/generate-scheduled-report.sh"
ln -sf "${INSTALL_DIR}/generate-scheduled-report.sh" /usr/bin/viatom-o2ring-generate-report

echo "==> Creating service user"
if ! id "${SERVICE_USER}" &>/dev/null; then
    useradd --system --no-create-home --user-group --groups plugdev "${SERVICE_USER}"
fi

echo "==> Seeding config"
mkdir -p "${CONFIG_DIR}"
if [[ -f "${CONFIG_DIR}/config.ini" ]]; then
    echo "    ${CONFIG_DIR}/config.ini already exists, leaving its contents as-is."
else
    cp "${REPO_DIR}/config/viatom-o2ring-daemon.ini.example" "${CONFIG_DIR}/config.ini"
    echo "    Wrote ${CONFIG_DIR}/config.ini -- edit it before (or after) starting the service."
fi
# The config can hold real secrets (API tokens, apprise_urls with embedded
# credentials), so it's only readable by the service account -- applied
# every run, not just on first write, in case it was ever loosened.
chown "${SERVICE_USER}:${SERVICE_USER}" "${CONFIG_DIR}/config.ini"
chmod 600 "${CONFIG_DIR}/config.ini"

echo "==> Installing systemd units"
cp "${REPO_DIR}/systemd/viatom-o2ring-daemon.service" /etc/systemd/system/
cp "${REPO_DIR}/systemd/viatom-o2ring-sync-files.service" /etc/systemd/system/
cp "${REPO_DIR}/systemd/viatom-o2ring-sync-files.timer" /etc/systemd/system/
cp "${REPO_DIR}/systemd/viatom-o2ring-report-generate.service" /etc/systemd/system/
cp "${REPO_DIR}/systemd/viatom-o2ring-report-generate.timer" /etc/systemd/system/
cp "${REPO_DIR}/systemd/viatom-o2ring-alert-check.service" /etc/systemd/system/
cp "${REPO_DIR}/systemd/viatom-o2ring-alert-check.timer" /etc/systemd/system/
cp "${REPO_DIR}/systemd/viatom-o2ring-api.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now viatom-o2ring-daemon

echo "==> Done. Edit ${CONFIG_DIR}/config.ini if you haven't, then watch discovery with:"
echo "        journalctl -u viatom-o2ring-daemon -f"
echo "==> Since the config is now owned by ${SERVICE_USER} (mode 600), running the CLI"
echo "    tools by hand needs sudo -u, e.g.:"
echo "        sudo -u ${SERVICE_USER} viatom-o2ring-report --config ${CONFIG_DIR}/config.ini"
echo "==> Stored-session file sync, scheduled report generation, alert checking, and"
echo "    the HTTP API are installed but not enabled (opt-in). To turn them on:"
echo "        sudo systemctl enable --now viatom-o2ring-sync-files.timer"
echo "        sudo systemctl enable --now viatom-o2ring-report-generate.timer"
echo "        sudo systemctl enable --now viatom-o2ring-alert-check.timer"
echo "        sudo systemctl enable --now viatom-o2ring-api.service"
