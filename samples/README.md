# Sample reports

PDF reports generated from a synthetic fixture (200 live readings over
~10 hours, SpO2 ranging 85-98%) using every `report.table_layout` /
`report.date_format` combination, via:

```ini
[report]
table_layout = full   # or compact, rollup
date_format = world    # or us
```

| File | table_layout | date_format |
|---|---|---|
| `full-world.pdf` | full | world |
| `full-us.pdf` | full | us |
| `compact-world.pdf` | compact | world |
| `compact-us.pdf` | compact | us |
| `rollup-world.pdf` | rollup | world |
| `rollup-us.pdf` | rollup | us |

Every sample includes the SpO2-category pie chart and the SpO2/pulse trend
charts (`report.include_chart = yes`) and the summary line
(`report.include_summary = yes`); only the reading table's shape differs.
None include a `sessions` table, since the fixture has no downloaded `.vld`
files -- `report.include_sessions = yes` adds one automatically once
sessions exist in the database.

`full-world-with-notes.pdf` is the same `full`/`world` fixture rendered
with a `[profile]` section set (`name`, `email`, `notes`), to show the
Wearer/Email/Notes lines that print below the generated-timestamp line
when those fields are configured -- normally handy for handing a printed
report to a doctor.

Regenerate with `viatom-o2ring-report` against your own database, or see
`scripts/make-fixture-db.py` for a minimal fixture to build one.
