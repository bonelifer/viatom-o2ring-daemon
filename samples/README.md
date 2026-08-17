# Sample reports

PDF reports generated from a fixed, deterministic fixture (20 live readings
across four weeks, drifting from Normal down through Mild/Moderate
hypoxemia and back, plus one downloaded overnight session) via:

```bash
./scripts/generate-samples.py
```

Run this after any change to `report.py`'s rendering, so these don't go
stale relative to what the code actually produces.

| File | Demonstrates |
|---|---|
| `full-world.pdf` | `table_layout = full`, `date_format = world` (the default shape) |
| `full-us.pdf` | `table_layout = full`, `date_format = us` |
| `compact-world.pdf` | `table_layout = compact`, `date_format = world` |
| `compact-us.pdf` | `table_layout = compact`, `date_format = us` |
| `rollup-world.pdf` | `table_layout = rollup`, `date_format = world` (one row per week) |
| `rollup-us.pdf` | `table_layout = rollup`, `date_format = us` |
| `full-minimal.pdf` | `include_address`/`include_categories`/`include_summary`/`include_sessions = no` |
| `chart-only.pdf` | `include_table`/`include_sessions = no` |
| `table-only.pdf` | `include_chart`/`include_sessions = no` |
| `full-world-with-notes.pdf` | a `[profile]` section set (`name`, `email`, `notes`), showing the Wearer/Email/Notes lines below the generated-timestamp line -- handy for handing a printed report to a doctor |
| `full-region-us.pdf` | `[profile] region = us` winning over a shared `[report]` default of `date_format = world, page_size = a4` -- the table renders `MM/DD/YYYY` on Letter-size pages despite the household default being world/A4 |

Every sample except the toggle demos above includes the SpO2-category pie
chart, the SpO2/pulse trend charts, the summary line, and the downloaded-
session summary table; only the specific toggle each demonstrates differs.
`include_address` (on in every sample but `full-minimal.pdf`) prints an
"Address:" line in the header rather than a table column -- see
`full-world.pdf`'s header vs. its reading table. Battery level isn't in
the reading table at all -- it's covered by `[alerting] low_battery_percent`
instead (see the main README's Alerting section); CSV export still
includes it as a column.

The fixture's one downloaded session isn't exported anywhere in these
samples -- see [Exporting a session's raw records](../README.md#exporting-a-sessions-raw-records)
for `viatom-o2ring-report --export-session` and the `/api/v1/session-records`
API endpoint, which pull the same data these summary rows are rolled up
from.
