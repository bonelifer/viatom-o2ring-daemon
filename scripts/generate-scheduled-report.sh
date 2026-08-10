#!/usr/bin/bash
# Generates a timestamped PDF report. Intended to be run on a schedule (the
# viatom-o2ring-report-generate.timer systemd unit, or a cron job) rather
# than invoked directly. Configure via environment variables, not flags,
# since a scheduler invokes this with a fixed command line.
set -e

CONFIG="${VIATOM_O2RING_CONFIG:-/etc/viatom-o2ring-daemon/config.ini}"
REPORT_DIR="${VIATOM_O2RING_REPORT_DIR:-/var/lib/viatom-o2ring-daemon/reports}"

mkdir -p "${REPORT_DIR}"
timestamp="$(date +%Y%m%d-%H%M%S)"
viatom-o2ring-report --config "${CONFIG}" --output "${REPORT_DIR}/report-${timestamp}.pdf"
