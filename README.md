# viatom-o2ring-daemon

![viatom-o2ring-daemon: pulse oximeter ring data over Bluetooth to a local home server and database](docs/images/viatom-o2ring-daemon-banner.png)

![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white) ![Bash](https://img.shields.io/badge/shell-Bash-4EAA25?logo=gnu-bash&logoColor=white) ![Bluetooth LE](https://img.shields.io/badge/Bluetooth-LE-0082FC?logo=bluetooth&logoColor=white)

[![License: GPL-3.0](https://img.shields.io/badge/license-GPL--3.0-blue)](https://github.com/home-health-hub/viatom-o2ring-daemon/blob/main/LICENSE) [![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/home-health-hub/viatom-o2ring-daemon#contributing) [![Discussions](https://img.shields.io/badge/discussions-welcome-blue)](https://github.com/home-health-hub/viatom-o2ring-daemon/discussions)

A standalone Linux daemon that connects to a Viatom/Wellue O2Ring (and
related ring-family) pulse oximeter over Bluetooth Low Energy (BLE), logs
live SpO2/pulse readings to a local SQLite database, and downloads stored
overnight/session recordings from the ring's onboard memory. No cloud
account, no companion app required.

It's a thin wrapper around the
[`viatom-o2ring-ble`](https://github.com/home-health-hub/viatom-o2ring-ble)
library, packaged to run unattended as a `systemd` service on something like
a Raspberry Pi sitting near the ring.

**Disclaimer: This is an unofficial, community-developed project. It is not
affiliated with, officially maintained by, or in any way officially
connected with Viatom Technology Co., Ltd. or Wellue. Nothing here is
medical advice; the SpO2 category labels and alerting are informational
only. Talk to a doctor about your oxygen saturation readings.**

**Work in progress -- not yet verified against real hardware.** This daemon
is built on `viatom-o2ring-ble`. Its legacy-protocol support (the default,
`monitor.protocol = legacy`) is protocol-correct-on-paper only as of this
writing (cross-checked against five independent sources but not yet
tested against an actual O2Ring-family device). Its O2Ring-S support
(`monitor.protocol = oxyii`) is ported from a source that *has* verified
its own implementation against real hardware -- a meaningfully stronger
starting point -- but the port itself, as wired into this daemon, hasn't
been independently re-verified here either. See `viatom-o2ring-ble`'s
`CLAUDE.md` and README for details on both.

## Supported devices

Two device families, selected via `monitor.protocol` in the config file
(see [Device protocol](#device-protocol)):

- `protocol = legacy` (the default): O2Ring, KidsO2, RingO2, and O2 Max --
  whatever `viatom-o2ring-ble`'s `O2RingClient` supports.
- `protocol = oxyii`: the O2Ring-S (T8520), which speaks a completely
  different BLE protocol -- via `viatom-o2ring-ble`'s `OxyIIClient`.

## Device protocol

```ini
[monitor]
protocol = legacy   # or oxyii
```

Everything downstream of live-reading capture -- storage, reports,
alerting, the HTTP API, pruning -- is protocol-agnostic; it only ever
operates on the rows already recorded in SQLite, not on which BLE
protocol produced them. Only two things actually branch on
`monitor.protocol`:

- **Live streaming** (`viatom-o2ring-daemon`): `RtReading`/`Reading`
  (legacy) or `OxyIIReading` (oxyii) are both flattened into the same
  `live_readings` row shape. OxyII readings have no perfusion-index or
  charge-state equivalent -- those columns are just left `NULL`, the same
  way they already tolerate a legacy reading missing one or the other.
- **Stored-session sync** (`viatom-o2ring-sync-files`): legacy `.vld`
  files and OxyII "Format A" recordings decode into different shapes
  (Format A has no embedded timestamp or duration -- only the
  recording's filename and record count) and get adapted into the same
  `sessions`/`session_records` tables either way. An OxyII recording
  that hasn't finished flushing its trailer yet is skipped and retried
  on the next sync, rather than stored with an incomplete summary --
  see `viatom-o2ring-ble`'s CLAUDE.md for why size alone can't tell the
  two states apart.

`monitor.legacy_sensors` (the older `CMD_READ_SENSORS` fallback) only
applies when `protocol = legacy`; it's ignored for `oxyii`, which has no
equivalent legacy command.

`--check-config` prints the resolved protocol so it's obvious which
device family a given config file targets.

A daemon instance already targets exactly one device (`monitor.address`),
so this is a single fixed choice per config file, not auto-detected. A
household with both device families runs one daemon instance per device,
same as it would for two rings of the same family.

## Features

- Scans for the device on first run, then pins its BLE address into the
  config file so future restarts connect directly instead of re-scanning
- Streams live readings (SpO2, pulse, battery, perfusion index, worn/
  calibrating state) to a local SQLite database
- Downloads stored `.vld` session files (e.g. overnight sleep recordings)
  from the ring's onboard memory that aren't already in the database, either
  automatically after each streaming session or on its own schedule
- Runs as a `systemd` service with automatic restart on failure
- Optional PDF/CSV reports: an SpO2-category time-distribution pie chart,
  SpO2/pulse trend charts, a reading table shaded by SpO2 category (choice
  of full, compact, or weekly/monthly rollup layouts), and a summary table
  of downloaded sessions
- Optional Apprise-based alerting on stale data, a low-SpO2 reading, or low
  battery
- Optional read-only HTTP API and MQTT publishing
- One optional `[profile]` section for the ring's wearer (report
  personalization, alert overrides) -- a ring has exactly one wearer at a
  time, so unlike a shared blood-pressure cuff or scale, there's no
  multi-person "who was this?" tagging to solve
- Supports two device families/protocols (`monitor.protocol = legacy` or
  `oxyii`) -- see [Device protocol](#device-protocol)

## Installation

Requires Python 3.11+.

### Quick install

```bash
git clone https://github.com/home-health-hub/viatom-o2ring-daemon.git
cd viatom-o2ring-daemon
sudo ./install.sh
```

This creates a venv at `/opt/viatom-o2ring-daemon`, installs the package
from the checkout, seeds `/etc/viatom-o2ring-daemon/config.ini` (if it
doesn't already exist), creates a `viatom-o2ring-daemon` system user, and
installs and enables the systemd service. It also installs (but does not
enable) the [stored-session file sync](#stored-session-files),
[scheduled report generation](#scheduled-report-generation), and
[alerting](#alerting) timer units, and the [HTTP API](#http-api) service.
It's safe to re-run: it skips steps that are already done. Edit the config
and `sudo systemctl restart viatom-o2ring-daemon` afterward.

`config.ini` can hold real secrets (API tokens, `apprise_urls` with embedded
credentials), so `install.sh` sets it to mode `600`, owned by the
`viatom-o2ring-daemon` user, every time it runs (including on re-runs, in
case it was ever loosened). Running the CLI tools by hand afterward needs
`sudo -u viatom-o2ring-daemon`, e.g.:

```bash
sudo -u viatom-o2ring-daemon viatom-o2ring-report --config /etc/viatom-o2ring-daemon/config.ini
```

### Manual install

```bash
python3 -m venv /opt/viatom-o2ring-daemon/venv
/opt/viatom-o2ring-daemon/venv/bin/pip install /path/to/viatom-o2ring-daemon  # this checkout
```

#### Config file

```bash
sudo mkdir -p /etc/viatom-o2ring-daemon
sudo cp config/viatom-o2ring-daemon.ini.example /etc/viatom-o2ring-daemon/config.ini
sudo "$EDITOR" /etc/viatom-o2ring-daemon/config.ini
```

Leave `[monitor] address` empty to auto-discover the device on first run
(power on the ring and keep it nearby while the daemon is scanning). Once
found, the daemon writes the address back into this file so it reconnects
directly on every future start. See
[config/viatom-o2ring-daemon.ini.example](config/viatom-o2ring-daemon.ini.example)
for every setting, with inline documentation.

Validate a config file without starting the daemon:

```bash
viatom-o2ring-daemon --config /etc/viatom-o2ring-daemon/config.ini --check-config
```

#### systemd service

```bash
sudo cp systemd/viatom-o2ring-daemon.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now viatom-o2ring-daemon
journalctl -u viatom-o2ring-daemon -f
```

### Stored session files

A ring can record measurement sessions (e.g. overnight sleep tracking) to
its own onboard memory even while not connected. `viatom-o2ring-sync-files`
connects once, downloads any `.vld` files not already in the local
database, and stores both the session summary (avg/min SpO2, desaturation
event counts, O2 score, steps) and its per-sample records.

This runs automatically after each streaming session ends (daemon stop, or
`--once`) unless `[file_sync] enabled = no`, since live streaming and file
transfer both compete for the ring's single BLE connection and aren't
interleaved mid-session. It can also run on its own schedule:

```bash
sudo cp systemd/viatom-o2ring-sync-files.service systemd/viatom-o2ring-sync-files.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now viatom-o2ring-sync-files.timer
```

Defaults to `OnCalendar=daily`. Requires `monitor.address` to already be
set (run the daemon at least once first, so the address is discovered and
persisted).

### Scheduled report generation

Optional and not enabled by default. Generates a timestamped PDF into
`/var/lib/viatom-o2ring-daemon/reports/` on a schedule:

```bash
sudo cp systemd/viatom-o2ring-report-generate.service systemd/viatom-o2ring-report-generate.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now viatom-o2ring-report-generate.timer
```

Defaults to `OnCalendar=weekly`. Configure via the `VIATOM_O2RING_CONFIG`
and `VIATOM_O2RING_REPORT_DIR` environment variables in the `.service` unit
rather than flags, since the timer invokes it with a fixed command line.

### Alerting

Also optional and not enabled by default. `viatom-o2ring-alert-check`
checks the latest reading for three conditions and notifies via
[Apprise](https://github.com/caronc/apprise) (100+ supported services:
Discord, Telegram, Slack, email, Pushover, generic webhooks, etc.) when
triggered:

- **Staleness**: no reading in over `stale_after_minutes` minutes.
- **Low SpO2**: the latest *worn* reading's SpO2 is at or below
  `low_spo2_percent`.
- **Low battery**: the latest reading's battery is at or below
  `low_battery_percent`.

```ini
[alerting]
enabled = yes
apprise_urls = tgram://bot_token/chat_id, mailto://user:password@gmail.com
stale_after_minutes = 30
low_spo2_percent = 88
low_battery_percent = 15
```

Run it periodically with the bundled timer:

```bash
sudo cp systemd/viatom-o2ring-alert-check.service systemd/viatom-o2ring-alert-check.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now viatom-o2ring-alert-check.timer
```

Defaults to `OnCalendar=*:0/15` (every 15 minutes). A repeat staleness alert
is throttled to at most once per hour while the condition persists; a
low-SpO2 or low-battery alert only fires once per newly-arrived reading,
not on every check. State is tracked per address in `alerting.state_path`
(default `/var/lib/viatom-o2ring-daemon/alert-state.json`). Delete it to
reset throttling. `--check-config` reports whether `[alerting]` is enabled
and how many URLs it parsed, without actually sending anything.

The `[profile]` section can override the destination and thresholds; see
[Per-profile alert routing](#per-profile-alert-routing).

### HTTP API

Also optional and not enabled by default. `viatom-o2ring-api` runs a small
read-only HTTP server exposing the same data as the other tools. It reads
the SQLite database directly and works whether or not the daemon is
currently running.

```ini
[api]
enabled = yes
host = 127.0.0.1
port = 8080
token =
```

```bash
sudo cp systemd/viatom-o2ring-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now viatom-o2ring-api.service
```

Endpoints:

| Method & path | Description |
|---|---|
| `GET /api/v1/health` | Unauthenticated liveness check: `{"status": "ok", "version": "..."}`. |
| `GET /api/v1/capabilities` | Unauthenticated description of what this daemon exposes (measurement types/modes, profile model, timestamp field meanings, MQTT config), for generic Health Hub-style clients. |
| `GET /api/v1/latest[?address=...]` | Most recent live reading, as JSON. |
| `GET /api/v1/sessions[?address=...&limit=...]` | Most recently downloaded sessions, newest first, as JSON. |
| `GET /api/v1/session-records?filename=...[&address=...&format=json\|csv]` | One session's raw per-sample records (the every-2-or-4-seconds data behind its summary). JSON by default; `format=csv` returns a file download. |
| `GET /api/v1/report[?format=pdf\|csv&period=...&from=...&to=...&address=...]` | Generates a report on demand using the same `[report]` config as `viatom-o2ring-report`, returned as a file download. |

```bash
curl http://127.0.0.1:8080/api/v1/latest
curl -o report.pdf "http://127.0.0.1:8080/api/v1/report?period=30d"
curl -o session.csv "http://127.0.0.1:8080/api/v1/session-records?filename=20260116233312.vld&format=csv"
```

**There's no TLS built in.** `host` defaults to `127.0.0.1` (loopback only)
for a reason: don't bind it to `0.0.0.0` or a LAN-facing interface without
putting a reverse proxy (with TLS and its own auth) in front of it. Setting
`api.token` requires an `Authorization: Bearer <token>` header on every
endpoint except `/api/v1/health` and `/api/v1/capabilities`, which is worth
doing even on loopback if other local users/processes on the same host
shouldn't see readings:

```bash
curl -H "Authorization: Bearer <token>" http://127.0.0.1:8080/api/v1/latest
```

If `api.host` isn't a loopback address and `api.token` is blank, both
`viatom-o2ring-api` (at startup) and `--check-config` print a warning. It's
not blocked outright, since a reverse proxy handling auth in front is a
legitimate setup, but forgetting to set a token before exposing the API on
the LAN is a plausible mistake worth surfacing rather than letting it pass
silently.

### Profile

Unlike a blood-pressure cuff or scale that a whole household shares, a ring
has exactly one wearer at a time, and this daemon already targets exactly
one device via `monitor.address`. So instead of a "who was this?" tagging
system, there's just one optional `[profile]` section describing the
wearer, used for report personalization and alert routing. A household with
more than one ring runs one daemon instance per ring (separate config file
and `db_path`).

```ini
[profile]
name = Jane Smith
email = jane@example.com
notes = Prescribed nighttime supplemental O2, target SpO2 >= 92%
region = us
apprise_urls = tgram://bot_token/jane_chat_id
stale_after_minutes = 30
low_spo2_percent = 90
```

- `name`/`email`/`notes` print below the report title, handy when handing a
  printed report to a doctor (`notes` for clinical context).
- `region` (`us` or `world`) sets both `date_format` and `page_size` at
  once, to the pairing normally used together (`us` -> US date format on
  letter paper; `world` -> world date format on A4), so this wearer's
  reports always come out right for where they are regardless of the
  shared `[report]` default -- e.g. a household default of `world`/A4, but
  `region = us` for the one family member visiting a US doctor.
- `date_format`/`page_size` each independently override `region` (if also
  set) or the matching `[report]` setting -- only needed for the rarer
  case of wanting one but not the other (e.g. US date format on A4 paper).
- `apprise_urls` **replaces** the global `[alerting] apprise_urls` for this
  wearer's alerts rather than adding to it. Leave blank to just use the
  global list.
- `stale_after_minutes`/`low_spo2_percent` override the matching
  `[alerting]` value; leave blank to inherit it. `low_battery_percent` is
  never overridden per profile -- it's a device fact, not a personal
  preference.

None of this is required. A config with no `[profile]` section at all still
records and reports normally, just without the personalization.

## Manual usage

### On-demand capture instead of a long-running service

```bash
viatom-o2ring-daemon --config /etc/viatom-o2ring-daemon/config.ini --once --once-timeout 60
```

Connects, waits up to `--once-timeout` seconds for a single reading, records
it, and exits. Exit code is `1` if no reading arrived in time. For when
you'd rather not run the daemon continuously.

## Database schema

`live_readings`, one row per streamed reading:

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER | Primary key |
| `recorded_at` | TEXT | ISO-8601 UTC timestamp the notification arrived |
| `address` | TEXT | Device BLE address |
| `spo2` | INTEGER | Blood oxygen saturation, percent |
| `pulse_bpm` | INTEGER | Pulse rate |
| `battery`, `battery_state` | INTEGER | Battery percent, charging status (0/1/2) |
| `perfusion_index` | INTEGER | Perfusion index |
| `worn`, `calibrating` | INTEGER | 0/1 flags |

`sessions`, one row per downloaded `.vld` file (deduplicated by
`(address, filename)`):

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER | Primary key |
| `address`, `filename` | TEXT | Device address and device-assigned file name |
| `downloaded_at` | TEXT | ISO-8601 UTC timestamp of the download |
| `start_time` | TEXT | Session start time, as reported by the device's own clock (not UTC-normalized) |
| `mode` | INTEGER | Recording mode (0 = sleep, 1 = monitor) |
| `duration_seconds` | INTEGER | Total recording duration |
| `spo2_avg`, `spo2_min` | INTEGER | Session SpO2 average/minimum |
| `spo2_below_3pct_events`, `spo2_below_4pct_events` | INTEGER | ODI-style desaturation event counts |
| `seconds_below_90pct`, `events_below_90pct`, `percent_below_90pct` | INTEGER/REAL | Time under 90% SpO2 |
| `o2_score` | REAL | Device-computed overall oxygen score (0-10) |
| `steps` | INTEGER | Pedometer step count |
| `record_count`, `resolution_seconds` | INTEGER/REAL | Sample count and interval |

`session_records`, one row per 5-byte sample in a session (cascade-deleted
with its parent session):

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER | Primary key |
| `session_id` | INTEGER | Foreign key into `sessions` |
| `time` | TEXT | Sample timestamp (device clock) |
| `spo2`, `heart_rate`, `acceleration` | INTEGER | Per-sample values |

## Reports

```bash
# Every reading on record
viatom-o2ring-report --config /etc/viatom-o2ring-daemon/config.ini

# Preset ranges: 7d, 30d, 90d, 1y, all (default: all)
viatom-o2ring-report --config /etc/viatom-o2ring-daemon/config.ini --period 30d

# Explicit date range (--to defaults to now if omitted)
viatom-o2ring-report --config /etc/viatom-o2ring-daemon/config.ini --from 2026-01-01 --to 2026-03-01

# Point directly at a database file instead of a config
viatom-o2ring-report --db /var/lib/viatom-o2ring-daemon/readings.db --format csv --output report.csv
```

PDF reports include a pie chart of time spent in each SpO2 category, SpO2
and pulse trend line charts, a reading table shaded by SpO2 category, a
downloaded-sessions summary table, and (if `report.include_summary = yes`)
an average/min/max summary with a category breakdown, handy to print and
bring to a doctor's appointment.

`report.include_chart`/`include_table`/`include_sessions` independently
toggle those sections off if you don't want them, and `report.table_layout`
picks the reading table's shape: `full` (one row per reading, the default),
`compact` (same per-reading detail, packed into 2 side-by-side column
groups), or `rollup` (one row per week/month: avg/min SpO2, average pulse,
reading count, and the worst SpO2 category seen that period). For a long
history, `rollup` paired with the chart is generally more useful than
paging through a continuous stream of readings.

Battery level isn't shown per-reading in the PDF table -- it's a device/
alerting concern (see [Alerting](#alerting)'s `low_battery_percent`), not a
clinically relevant data point to page through. CSV export still includes
it as a column for anyone doing their own analysis.

`report.exclude_not_worn` (default yes) skips readings where the device
reported the ring as not worn -- an idle/off-finger reading isn't a
clinically meaningful data point, and including it would skew averages and
the category distribution.

See [samples/single/](samples/single/) for a rendered PDF of every layout/
date-format combination.

### Exporting a session's raw records

The sessions table/summary shows avg/min SpO2, desaturation event counts,
and so on for each downloaded `.vld` file, but that's a rollup. To get the
underlying every-2-or-4-seconds data behind one specific session (e.g. to
plot an overnight recording yourself), export it as CSV instead of
generating a reading report:

```bash
viatom-o2ring-report --config /etc/viatom-o2ring-daemon/config.ini \
  --export-session 20260116233312.vld --output session.csv
```

`--export-session` ignores `--format`/`--period`/`--from`/`--to` -- it
always writes CSV, and `--output` still applies (default:
`<filename>-records.csv`). The same data is available live via the HTTP
API's `GET /api/v1/session-records?filename=...` (JSON by default,
`format=csv` for a file download); see [HTTP API](#http-api).

## Pruning old data

```bash
# See how many rows older than 365 days would be deleted
viatom-o2ring-prune --config /etc/viatom-o2ring-daemon/config.ini --older-than 365

# Actually delete them (also reclaims disk space with VACUUM)
viatom-o2ring-prune --config /etc/viatom-o2ring-daemon/config.ini --older-than 365 --yes
```

Prunes both `live_readings` and `sessions` (with their `session_records`
cascade-deleted).

## MQTT

```ini
[mqtt]
enabled = yes
host = mqtt.example.com
topic_prefix = viatom_o2ring_daemon
```

Each live reading publishes as JSON to `<topic_prefix>/<device address>/state`.
A broker outage is logged and non-fatal; it never blocks local recording to
SQLite.

## Troubleshooting

- **Device never discovered**: make sure it's powered on and nearby while
  the daemon is scanning, and that no other app (e.g. the official Viatom/
  Wellue app) is already connected to it: the device only accepts one
  connection at a time.
- **`No Bluetooth scanner available`**: check `bluetoothctl` shows an
  adapter, and that the `viatom-o2ring-daemon` system user is in the
  `bluetooth` group (the systemd unit sets `SupplementaryGroups=bluetooth`).
- **RT_DATA readings look wrong** (`protocol = legacy` only): try
  `monitor.legacy_sensors = yes` to fall back to the older
  `CMD_READ_SENSORS` command.
- **O2Ring-S file sync never finds anything, or a session never appears**
  (`protocol = oxyii` only): a recording that hasn't finalized yet (its
  trailer hasn't flushed) is deliberately skipped rather than stored
  incomplete -- it'll show up on a later sync once the ring finishes
  writing it. See [Device protocol](#device-protocol).
- **Config errors**: run `--check-config` for a section-by-section report of
  what's wrong.

## Acknowledgments

- Built on
  [`viatom-o2ring-ble`](https://github.com/home-health-hub/viatom-o2ring-ble),
  which cross-checks five independent community/official sources for its
  legacy-protocol decoding, and separately ports its O2Ring-S (OxyII)
  support from
  [nglessner/o2ring-s-protocol](https://github.com/nglessner/o2ring-s-protocol)
  -- see that repo's README and `CLAUDE.md`.
- Project layout modeled on
  [`etekcity-scale-daemon`](https://github.com/home-health-hub/etekcity-scale-daemon)
  and [`etekcity-bp-daemon`](https://github.com/home-health-hub/etekcity-bp-daemon).
- Code review, implementation, and documentation assisted by
  [Claude](https://www.anthropic.com/claude).

## Contributing

Contributions are welcome!

- **Bug reports**: [Open an issue](https://github.com/home-health-hub/viatom-o2ring-daemon/issues).
- **Everything else** (questions, feature requests, ideas, general discussion): [Use Discussions](https://github.com/home-health-hub/viatom-o2ring-daemon/discussions).
- Pull requests are welcome for bug fixes or discussed features.

## License

This project is licensed under the **GNU General Public License v3.0**.

See [LICENSE](LICENSE) for more information.
