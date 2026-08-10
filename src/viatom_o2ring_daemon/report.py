"""Generate a PDF or CSV report of O2Ring readings from the SQLite database."""

from __future__ import annotations

import argparse
import csv
import sqlite3
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from xml.sax.saxutils import escape

from reportlab.graphics.charts.legends import Legend
from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics.charts.piecharts import Pie
from reportlab.graphics.shapes import Drawing, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from ._version import __version__
from .categories import MILD, MODERATE, NORMAL, SEVERE, classify
from .config import (
    DEFAULT_PROFILE_CONFIG,
    DEFAULT_REPORT_CONFIG,
    REGION_REPORT_DEFAULTS,
    ConfigError,
    ProfileConfig,
    ReportConfig,
    load_config,
    load_profile_config,
    load_report_config,
)
from .storage import ensure_schema

_PERIOD_DAYS = {"7d": 7, "30d": 30, "90d": 90, "1y": 365}

_PAGE_SIZES = {"letter": letter, "a4": A4}

# Date/time strftime patterns for each date_format preset.
_DATE_TIME_FORMATS = {
    "us": "%m/%d/%Y %I:%M:%S %p",
    "world": "%d/%m/%Y %H:%M:%S",
}

# Maximum number of x-axis date labels to show on a chart before thinning
# them out, so labels don't overlap when there are many readings.
_CHART_MAX_LABELS = 10

# SpO2 category -> background color for table rows / pie slices.
_CATEGORY_COLORS = {
    NORMAL: colors.HexColor("#d9ead3"),
    MILD: colors.HexColor("#fff2cc"),
    MODERATE: colors.HexColor("#fce5cd"),
    SEVERE: colors.HexColor("#cc0000"),
}

# SpO2 category -> severity rank, higher is worse. Used to pick the "worst"
# category within a rollup period.
_CATEGORY_SEVERITY = {NORMAL: 0, MILD: 1, MODERATE: 2, SEVERE: 3}

_CATEGORY_ORDER = (NORMAL, MILD, MODERATE, SEVERE)


def _format_datetime(recorded_at: datetime, date_format: str) -> str:
    """Format a UTC timestamp in local time using the given date_format preset."""
    return recorded_at.astimezone().strftime(_DATE_TIME_FORMATS[date_format])


@dataclass
class ReportRow:
    """One live reading row as read back from the database."""

    recorded_at: datetime
    address: str
    spo2: int | None
    pulse_bpm: int | None
    battery: int | None
    battery_state: int | None
    perfusion_index: int | None
    worn: bool
    calibrating: bool


@dataclass
class SessionRow:
    """One downloaded session summary row as read back from the database."""

    filename: str
    start_time: datetime
    duration_seconds: int
    spo2_avg: int
    spo2_min: int
    spo2_below_3pct_events: int
    spo2_below_4pct_events: int
    o2_score: float
    steps: int


@dataclass
class SessionRecordRow:
    """One raw per-sample record from a downloaded session, as read back from the database."""

    time: datetime
    spo2: int
    heart_rate: int
    acceleration: int


def _resolve_range(
    period: str, from_date: str | None, to_date: str | None
) -> tuple[datetime | None, datetime | None]:
    """Resolve the requested period/from/to options into a UTC datetime range."""
    if from_date:
        start = datetime.strptime(from_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end = (
            datetime.strptime(to_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            + timedelta(days=1)
            if to_date
            else datetime.now(timezone.utc)
        )
        return start, end

    if period == "all":
        return None, None

    days = _PERIOD_DAYS[period]
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    return start, end


def fetch_rows(
    db_path: str,
    address: str | None,
    start: datetime | None,
    end: datetime | None,
    exclude_not_worn: bool = True,
) -> list[ReportRow]:
    """Query live readings from the database within an optional address/date range.

    Args:
        db_path: Path to the SQLite database file.
        address: Restrict to a single device's BLE address, if given.
        start: Inclusive UTC start of the date range, or None for no lower bound.
        end: Exclusive UTC end of the date range, or None for no upper bound.
        exclude_not_worn: Skip readings where the device reported the ring
            as not worn -- an idle/off-finger reading isn't a clinically
            meaningful data point, and including it would skew averages and
            the SpO2 category distribution.

    Returns:
        Matching rows ordered oldest first.
    """
    query = (
        "SELECT recorded_at, address, spo2, pulse_bpm, battery, battery_state, "
        "perfusion_index, worn, calibrating FROM live_readings"
    )
    clauses: list[str] = []
    params: list[str] = []

    if address:
        clauses.append("address = ?")
        params.append(address)
    if exclude_not_worn:
        clauses.append("worn = 1")
    if start is not None:
        clauses.append("recorded_at >= ?")
        params.append(start.isoformat())
    if end is not None:
        clauses.append("recorded_at < ?")
        params.append(end.isoformat())

    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY recorded_at ASC"

    connection = sqlite3.connect(db_path)
    try:
        cursor = connection.execute(query, params)
        return [
            ReportRow(
                recorded_at=datetime.fromisoformat(row[0]),
                address=row[1],
                spo2=row[2],
                pulse_bpm=row[3],
                battery=row[4],
                battery_state=row[5],
                perfusion_index=row[6],
                worn=bool(row[7]),
                calibrating=bool(row[8]),
            )
            for row in cursor.fetchall()
        ]
    finally:
        connection.close()


def fetch_sessions(
    db_path: str, address: str | None, start: datetime | None, end: datetime | None
) -> list[SessionRow]:
    """Query downloaded session summaries within an optional address/date range.

    Args:
        db_path: Path to the SQLite database file.
        address: Restrict to a single device's BLE address, if given.
        start: Inclusive UTC start of the date range, or None for no lower bound.
        end: Exclusive UTC end of the date range, or None for no upper bound.

    Returns:
        Matching sessions ordered oldest first. ``start_time`` is the
        device's own clock at recording time (naive, not UTC-normalized --
        see VldHeader.start_time), so it's rendered as-is rather than
        converted with ``astimezone()`` like live-reading timestamps.
    """
    query = (
        "SELECT filename, start_time, duration_seconds, spo2_avg, spo2_min, "
        "spo2_below_3pct_events, spo2_below_4pct_events, o2_score, steps FROM sessions"
    )
    clauses: list[str] = []
    params: list[str] = []

    if address:
        clauses.append("address = ?")
        params.append(address)
    if start is not None:
        clauses.append("start_time >= ?")
        params.append(start.replace(tzinfo=None).isoformat())
    if end is not None:
        clauses.append("start_time < ?")
        params.append(end.replace(tzinfo=None).isoformat())

    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY start_time ASC"

    connection = sqlite3.connect(db_path)
    try:
        cursor = connection.execute(query, params)
        return [
            SessionRow(
                filename=row[0],
                start_time=datetime.fromisoformat(row[1]),
                duration_seconds=row[2],
                spo2_avg=row[3],
                spo2_min=row[4],
                spo2_below_3pct_events=row[5],
                spo2_below_4pct_events=row[6],
                o2_score=row[7],
                steps=row[8],
            )
            for row in cursor.fetchall()
        ]
    finally:
        connection.close()


def fetch_session_records(
    db_path: str, filename: str, address: str | None = None
) -> list[SessionRecordRow]:
    """Query one downloaded session's raw per-sample records.

    The session summary (see ``fetch_sessions``) is a rollup; this is the
    underlying every-2-or-4-seconds data it was computed from, for anyone
    who wants to actually plot or re-analyze a specific overnight/session
    recording rather than just see its summary stats.

    Args:
        db_path: Path to the SQLite database file.
        filename: The device-assigned file name (e.g. "20260116233312.vld").
        address: Disambiguate if the same filename was ever downloaded from
            more than one device's address, if given.

    Returns:
        Matching records ordered oldest first, empty if no session matches
        ``filename`` (and ``address``, if given). ``time`` is the device's
        own clock (naive, not UTC-normalized -- see ``fetch_sessions``), so
        it's rendered as-is rather than converted with ``astimezone()``.
    """
    query = (
        "SELECT session_records.time, session_records.spo2, "
        "session_records.heart_rate, session_records.acceleration "
        "FROM session_records JOIN sessions ON sessions.id = session_records.session_id "
        "WHERE sessions.filename = ?"
    )
    params: list[str] = [filename]
    if address:
        query += " AND sessions.address = ?"
        params.append(address)
    query += " ORDER BY session_records.time ASC"

    connection = sqlite3.connect(db_path)
    try:
        cursor = connection.execute(query, params)
        return [
            SessionRecordRow(
                time=datetime.fromisoformat(row[0]),
                spo2=row[1],
                heart_rate=row[2],
                acceleration=row[3],
            )
            for row in cursor.fetchall()
        ]
    finally:
        connection.close()


def _apply_profile_overrides(
    report_config: ReportConfig, profile_config: ProfileConfig
) -> ReportConfig:
    """Apply a profile's region/date_format/page_size overrides onto report_config.

    ``region`` (if set) supplies both date_format and page_size at once --
    the common case of "this wearer's reports should always look right for
    where they are," regardless of what the shared [report] default is set
    to. An explicit date_format/page_size on the same profile still wins
    over its region, for the rarer case of wanting one but not the other
    (e.g. US date format on A4 paper).
    """
    overrides = {}
    if profile_config.region:
        region_date_format, region_page_size = REGION_REPORT_DEFAULTS[profile_config.region]
        overrides["date_format"] = region_date_format
        overrides["page_size"] = region_page_size
    if profile_config.date_format:
        overrides["date_format"] = profile_config.date_format
    if profile_config.page_size:
        overrides["page_size"] = profile_config.page_size
    return replace(report_config, **overrides) if overrides else report_config


def build_csv(rows: list[ReportRow], output_path: str, report_config: ReportConfig) -> None:
    """Write reading rows to a CSV file."""
    header = ["Date/Time (local)"]
    if report_config.include_address:
        header.append("Address")
    header.extend(["SpO2 (%)", "Pulse (bpm)", "Battery (%)", "Perfusion Index"])
    if report_config.include_categories:
        header.append("Category")
    header.extend(["Worn", "Calibrating"])

    with open(output_path, "w", newline="") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(header)
        for row in rows:
            values: list[object] = [_format_datetime(row.recorded_at, report_config.date_format)]
            if report_config.include_address:
                values.append(row.address)
            values.extend(
                [
                    row.spo2 if row.spo2 is not None else "",
                    row.pulse_bpm if row.pulse_bpm is not None else "",
                    row.battery if row.battery is not None else "",
                    row.perfusion_index if row.perfusion_index is not None else "",
                ]
            )
            if report_config.include_categories:
                values.append(classify(row.spo2) or "")
            values.extend(["yes" if row.worn else "no", "yes" if row.calibrating else "no"])
            writer.writerow(values)


def build_session_records_csv(records: list[SessionRecordRow], output_path: str) -> None:
    """Write one session's raw per-sample records to a CSV file.

    Args:
        records: Records to include, oldest first (see ``fetch_session_records``).
        output_path: Filesystem path to write the CSV to.
    """
    with open(output_path, "w", newline="") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(["Time (device clock)", "SpO2 (%)", "Heart Rate (bpm)", "Acceleration"])
        for record in records:
            writer.writerow(
                [
                    record.time.strftime("%Y-%m-%d %H:%M:%S"),
                    record.spo2,
                    record.heart_rate,
                    record.acceleration,
                ]
            )


def _header_style_commands() -> list[tuple]:
    """Return the header/grid/font style commands shared by every table layout."""
    return [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2f5d8a")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
    ]


def _build_table(rows: list[ReportRow], report_config: ReportConfig) -> Table:
    """Build the PDF reading table, with rows shaded by SpO2 category.

    The device address isn't a column here -- see ``_address_elements``,
    which prints it once in the header instead, since it's constant across
    a report's rows far more often than not (one daemon instance already
    targets one device) and repeating it on every row wasted table width.
    """
    header = ["Date/Time (local)"]
    numeric_start = len(header)
    header.extend(["SpO2\n(%)", "Pulse\n(bpm)", "Battery\n(%)"])
    if report_config.include_categories:
        header.append("Category")

    data = [header]
    categories: list[str | None] = []
    for row in rows:
        category = classify(row.spo2)
        categories.append(category)

        values: list[object] = [_format_datetime(row.recorded_at, report_config.date_format)]
        values.extend(
            [
                row.spo2 if row.spo2 is not None else "-",
                row.pulse_bpm if row.pulse_bpm is not None else "-",
                row.battery if row.battery is not None else "-",
            ]
        )
        if report_config.include_categories:
            values.append(category or "-")
        data.append(values)

    numeric_cols = list(range(numeric_start, numeric_start + 3))
    style_commands = _header_style_commands()
    style_commands.extend(("ALIGN", (idx, 1), (idx, -1), "RIGHT") for idx in numeric_cols)
    for row_index, category in enumerate(categories, start=1):
        color = _CATEGORY_COLORS.get(category, colors.white)
        style_commands.append(("BACKGROUND", (0, row_index), (-1, row_index), color))
        if category == SEVERE:
            style_commands.append(("TEXTCOLOR", (0, row_index), (-1, row_index), colors.white))

    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle(style_commands))
    return table


_COMPACT_LAYOUT_COLUMN_GROUPS = 2


def _build_compact_table(rows: list[ReportRow], report_config: ReportConfig) -> Table:
    """Build the compact layout: Date/SpO2/Pulse/Battery only, side by side."""
    groups = min(_COMPACT_LAYOUT_COLUMN_GROUPS, len(rows))
    rows_per_column = -(-len(rows) // groups)  # ceil division

    group_header = ["Date/Time", "SpO2 (%)", "Pulse", "Battery (%)"]
    header = group_header * groups
    data = [header]
    for r in range(rows_per_column):
        line: list[object] = []
        for g in range(groups):
            idx = g * rows_per_column + r
            if idx < len(rows):
                row = rows[idx]
                line.append(_format_datetime(row.recorded_at, report_config.date_format))
                line.append(row.spo2 if row.spo2 is not None else "-")
                line.append(row.pulse_bpm if row.pulse_bpm is not None else "-")
                line.append(row.battery if row.battery is not None else "-")
            else:
                line.extend(["", "", "", ""])
        data.append(line)

    align_cols = [i for i in range(len(header)) if i % 4 in (1, 2, 3)]
    style_commands = _header_style_commands()
    style_commands.extend(("ALIGN", (idx, 1), (idx, -1), "RIGHT") for idx in align_cols)

    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle(style_commands))
    return table


def _rollup_key(recorded_at: datetime, period: str) -> tuple[int, int]:
    """Return the (year, period-number) bucket a reading's local time falls in."""
    local = recorded_at.astimezone()
    if period == "month":
        return (local.year, local.month)
    iso_year, iso_week, _ = local.isocalendar()
    return (iso_year, iso_week)


def _rollup_label(key: tuple[int, int], period: str) -> str:
    """Render a rollup bucket key as a human-readable period label."""
    if period == "month":
        year, month = key
        return date(year, month, 1).strftime("%B %Y")
    iso_year, iso_week = key
    monday = date.fromisocalendar(iso_year, iso_week, 1)
    sunday = monday + timedelta(days=6)
    return f"{monday.strftime('%m/%d')}-{sunday.strftime('%m/%d')}/{iso_year}"


def _range_str(values: list[float]) -> str:
    """Format a list of values as "avg (min-max)", or "-" if empty."""
    if not values:
        return "-"
    return f"{sum(values) / len(values):.0f} ({min(values):.0f}-{max(values):.0f})"


def _build_rollup_buckets(
    rows: list[ReportRow], period: str
) -> dict[tuple[int, int], list[ReportRow]]:
    """Group reading rows into weekly or monthly buckets."""
    buckets: dict[tuple[int, int], list[ReportRow]] = {}
    for row in rows:
        key = _rollup_key(row.recorded_at, period)
        buckets.setdefault(key, []).append(row)
    return dict(sorted(buckets.items(), key=lambda item: item[0]))


def _build_rollup_table(rows: list[ReportRow], report_config: ReportConfig) -> Table:
    """Build the rollup layout: one row per week/month instead of per reading."""
    buckets = _build_rollup_buckets(rows, report_config.rollup_period)

    header = ["Period", "Readings", "SpO2\navg (min-max) %", "Pulse avg\n(bpm)"]
    if report_config.include_categories:
        header.append("Worst\nCategory")

    data = [header]
    worst_categories: list[str | None] = []
    for period_key, bucket_rows in buckets.items():
        spo2_values = [r.spo2 for r in bucket_rows if r.spo2 is not None]
        pulse_values = [r.pulse_bpm for r in bucket_rows if r.pulse_bpm is not None]

        worst_category = None
        worst_rank = -1
        for row in bucket_rows:
            category = classify(row.spo2)
            rank = _CATEGORY_SEVERITY.get(category, -1)
            if rank > worst_rank:
                worst_rank = rank
                worst_category = category
        worst_categories.append(worst_category)

        values: list[object] = [
            _rollup_label(period_key, report_config.rollup_period),
            len(bucket_rows),
            _range_str(spo2_values),
            f"{sum(pulse_values) / len(pulse_values):.0f}" if pulse_values else "-",
        ]
        if report_config.include_categories:
            values.append(worst_category or "-")
        data.append(values)

    numeric_cols = [1, 2, 3]
    style_commands = _header_style_commands()
    style_commands.extend(("ALIGN", (idx, 1), (idx, -1), "RIGHT") for idx in numeric_cols)
    if report_config.include_categories:
        for row_index, category in enumerate(worst_categories, start=1):
            color = _CATEGORY_COLORS.get(category, colors.white)
            style_commands.append(("BACKGROUND", (0, row_index), (-1, row_index), color))
            if category == SEVERE:
                style_commands.append(
                    ("TEXTCOLOR", (0, row_index), (-1, row_index), colors.white)
                )

    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle(style_commands))
    return table


def _build_sessions_table(sessions: list[SessionRow], report_config: ReportConfig) -> Table:
    """Build a summary table of downloaded overnight/session recordings."""
    header = [
        "Start (device time)", "Duration", "SpO2\navg/min (%)", "Desat\nevents (3%/4%)",
        "O2 Score", "Steps",
    ]
    data = [header]
    for session in sessions:
        hours, remainder = divmod(session.duration_seconds, 3600)
        minutes = remainder // 60
        data.append(
            [
                session.start_time.strftime(
                    "%m/%d/%Y %H:%M" if report_config.date_format == "us" else "%d/%m/%Y %H:%M"
                ),
                f"{hours}h {minutes}m",
                f"{session.spo2_avg} / {session.spo2_min}",
                f"{session.spo2_below_3pct_events} / {session.spo2_below_4pct_events}",
                f"{session.o2_score:.1f}",
                session.steps,
            ]
        )

    style_commands = _header_style_commands()
    style_commands.extend(("ALIGN", (idx, 1), (idx, -1), "RIGHT") for idx in (1, 2, 3, 4, 5))
    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle(style_commands))
    return table


def _build_spo2_category_pie(rows: list[ReportRow]) -> Drawing:
    """Build a pie chart of time spent in each SpO2 category, with a legend.

    Mirrors the same "time in range" presentation used for glucose readings
    in trividia-truemetrix-daemon, applied to the standard hypoxemia bands
    instead of a configurable target range -- these bands are a fixed
    clinical definition (see categories.py), not something to override.
    """
    counts = {category: 0 for category in _CATEGORY_ORDER}
    for row in rows:
        category = classify(row.spo2)
        if category is not None:
            counts[category] += 1
    total = sum(counts.values())

    drawing = Drawing(480, 180)
    if total == 0:
        drawing.add(String(10, 90, "No SpO2 data to plot."))
        return drawing

    present = [category for category in _CATEGORY_ORDER if counts[category] > 0]

    pie = Pie()
    pie.x = 30
    pie.y = 10
    pie.width = 140
    pie.height = 140
    pie.data = [counts[category] for category in present]
    pie.labels = [f"{counts[category] * 100 / total:.0f}%" for category in present]
    pie.slices.strokeWidth = 0.5
    for i, category in enumerate(present):
        pie.slices[i].fillColor = _CATEGORY_COLORS[category]

    legend = Legend()
    legend.x = 220
    legend.y = 140
    legend.alignment = "right"
    legend.columnMaximum = len(present)
    legend.colorNamePairs = [
        (_CATEGORY_COLORS[category], f"{category} ({counts[category]})") for category in present
    ]

    drawing.add(pie)
    drawing.add(legend)
    drawing.add(
        String(30, 165, "Time in each SpO2 category (worn readings)",
               fontName="Helvetica-Bold", fontSize=10)
    )
    return drawing


def _build_trend_chart(
    rows: list[ReportRow], report_config: ReportConfig, value_of, label: str,
    color: colors.Color, value_min: float, value_max: float,
) -> Drawing:
    """Build a single-series line chart of one metric (SpO2 or pulse) over time.

    Args:
        rows: Reading rows to include, oldest first.
        report_config: Supplies the date/time format.
        value_of: Extracts the numeric value to plot from a ReportRow.
        label: Chart caption, e.g. "SpO2 (%)".
        color: Line color.
        value_min: Y-axis floor to add below the data's own minimum.
        value_max: Y-axis ceiling to add above the data's own maximum.

    Returns:
        A reportlab Drawing, or just a "not enough data" note if fewer than
        two readings have a value for this metric.
    """
    valued_rows = [row for row in rows if value_of(row) is not None]
    if len(valued_rows) < 2:
        drawing = Drawing(480, 150)
        drawing.add(String(10, 75, f"Not enough {label} data to plot a chart."))
        return drawing

    reference_date = valued_rows[0].recorded_at

    def day_offset(row: ReportRow) -> float:
        return (row.recorded_at - reference_date).total_seconds() / 86400

    points = [(day_offset(row), value_of(row)) for row in valued_rows]

    drawing = Drawing(480, 150)
    chart = LinePlot()
    chart.x = 50
    chart.y = 25
    chart.width = 400
    chart.height = 95
    chart.data = [points]
    chart.lines[0].strokeColor = color
    chart.lines[0].strokeWidth = 1.5

    values = [v for _, v in points]
    chart.yValueAxis.valueMin = min(values) - value_min
    chart.yValueAxis.valueMax = max(values) + value_max

    days = [x for x, _ in points]
    chart.xValueAxis.valueMin = min(days)
    chart.xValueAxis.valueMax = max(days)
    span_days = max(days) - min(days)
    if span_days > 0:
        chart.xValueAxis.valueStep = max(1, span_days // _CHART_MAX_LABELS + 1)
    date_pattern = "%m/%d" if report_config.date_format == "us" else "%d/%m"
    chart.xValueAxis.labelTextFormat = lambda value: (
        (reference_date + timedelta(days=value)).astimezone().strftime(date_pattern)
    )

    drawing.add(chart)
    drawing.add(
        String(chart.x, chart.y + chart.height + 15, label,
               fontName="Helvetica-Bold", fontSize=10)
    )
    return drawing


def _address_lines(rows: list[ReportRow]) -> list[str]:
    """Build one "Address: <address>" line per distinct device address in the rows.

    Almost always just one line -- a daemon instance already targets a
    single device -- but the database can in principle hold more than one
    address (e.g. reused across a device swap; see ``fetch_rows``), so this
    doesn't just assume the first row's address applies to the whole report.

    Args:
        rows: Reading rows to include.

    Returns:
        Text lines, empty if ``rows`` is empty. A single address renders as
        one "Address: ..." line; more than one renders as "Addresses:"
        followed by one indented line per address.
    """
    addresses = sorted({row.address for row in rows})
    if not addresses:
        return []
    if len(addresses) == 1:
        return [f"Address: {addresses[0]}"]
    return ["Addresses:"] + [f"&nbsp;&nbsp;{address}" for address in addresses]


def _summary_lines(rows: list[ReportRow], report_config: ReportConfig) -> list[str]:
    """Build min/max/average text lines for SpO2, pulse, and category breakdown."""
    spo2_values = [row.spo2 for row in rows if row.spo2 is not None]
    pulse_values = [row.pulse_bpm for row in rows if row.pulse_bpm is not None]
    if not spo2_values:
        return []

    lines = [
        f"SpO2: avg {sum(spo2_values) / len(spo2_values):.0f}%, "
        f"min {min(spo2_values)}%, max {max(spo2_values)}%",
    ]
    if pulse_values:
        lines.append(
            f"Pulse: avg {sum(pulse_values) / len(pulse_values):.0f} bpm, "
            f"min {min(pulse_values)}, max {max(pulse_values)} bpm"
        )

    if report_config.include_categories:
        counts: dict[str, int] = {}
        for row in rows:
            category = classify(row.spo2)
            if category:
                counts[category] = counts.get(category, 0) + 1
        if counts:
            breakdown = ", ".join(f"{name}: {count}" for name, count in counts.items())
            lines.append(f"Category breakdown: {breakdown}")

    return lines


def build_pdf(
    rows: list[ReportRow],
    output_path: str,
    report_config: ReportConfig = DEFAULT_REPORT_CONFIG,
    profile_config: ProfileConfig = DEFAULT_PROFILE_CONFIG,
    sessions: list[SessionRow] = (),
) -> None:
    """Render reading rows as a chart, summary, and table in a PDF file.

    Args:
        rows: Reading rows to include, oldest first.
        output_path: Filesystem path to write the PDF to.
        report_config: Controls which columns/sections are shown, the
            date/time format, the page size, and (if the table is included)
            which layout it renders as (full/compact/rollup).
        profile_config: Optional wearer name/email/notes to print below the
            title (fields left blank are omitted).
        sessions: Downloaded overnight/session summaries to list, if
            ``report_config.include_sessions`` is set.
    """
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(output_path, pagesize=_PAGE_SIZES[report_config.page_size])
    elements = [
        Paragraph("O2Ring Pulse Oximeter Report", styles["Title"]),
        Paragraph(
            f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
            f" &middot; {len(rows)} reading(s)",
            styles["Normal"],
        ),
    ]
    if report_config.include_address:
        elements.extend(
            Paragraph(line, styles["Normal"]) for line in _address_lines(rows)
        )
    if profile_config.name:
        elements.append(Paragraph(f"Wearer: {escape(profile_config.name)}", styles["Normal"]))
    if profile_config.email:
        elements.append(Paragraph(f"Email: {escape(profile_config.email)}", styles["Normal"]))
    if profile_config.notes:
        elements.append(Paragraph(f"Notes: {escape(profile_config.notes)}", styles["Normal"]))
    if report_config.include_summary and rows:
        elements.extend(
            Paragraph(line, styles["Normal"]) for line in _summary_lines(rows, report_config)
        )
    elements.append(Spacer(1, 0.2 * inch))

    if report_config.include_chart and rows:
        elements.append(_build_spo2_category_pie(rows))
        elements.append(Spacer(1, 0.1 * inch))
        elements.append(
            _build_trend_chart(
                rows, report_config, lambda r: r.spo2, "SpO2 (%) over time",
                colors.HexColor("#cc0000"), 3, 3,
            )
        )
        elements.append(Spacer(1, 0.1 * inch))
        elements.append(
            _build_trend_chart(
                rows, report_config, lambda r: r.pulse_bpm, "Pulse (bpm) over time",
                colors.HexColor("#2f5d8a"), 5, 5,
            )
        )
        elements.append(Spacer(1, 0.2 * inch))

    if report_config.include_table and rows:
        if report_config.table_layout == "compact":
            elements.append(_build_compact_table(rows, report_config))
        elif report_config.table_layout == "rollup":
            elements.append(_build_rollup_table(rows, report_config))
        else:
            elements.append(_build_table(rows, report_config))

    if report_config.include_sessions and sessions:
        elements.append(Spacer(1, 0.2 * inch))
        elements.append(Paragraph("Recorded Sessions", styles["Heading2"]))
        elements.append(_build_sessions_table(sessions, report_config))

    doc.build(elements)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="viatom-o2ring-report",
        description="Generate a PDF or CSV report from the daemon's reading database.",
    )
    parser.add_argument(
        "-V", "--version", action="version", version=f"%(prog)s {__version__}"
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "-c", "--config", help="Path to the daemon's INI config file (reads db_path from it)"
    )
    source.add_argument(
        "-d", "--db", help="Path to the SQLite database file, bypassing the config file"
    )
    parser.add_argument(
        "-F", "--format", choices=["pdf", "csv"], default="pdf",
        help="Output format (default: %(default)s)",
    )
    parser.add_argument(
        "-o", "--output", help="Output file path (default: o2ring-report.<format>)"
    )
    parser.add_argument(
        "-p", "--period", choices=["7d", "30d", "90d", "1y", "all"], default="all",
        help="Preset date range (default: %(default)s)",
    )
    parser.add_argument(
        "-f", "--from", dest="from_date", metavar="YYYY-MM-DD",
        help="Explicit start date, overrides --period",
    )
    parser.add_argument(
        "-t", "--to", dest="to_date", metavar="YYYY-MM-DD",
        help="Explicit end date (inclusive), defaults to now",
    )
    parser.add_argument(
        "-a", "--address", help="Restrict the report to one device's BLE address"
    )
    parser.add_argument(
        "-s",
        "--export-session",
        dest="export_session",
        metavar="FILENAME",
        help=(
            "Export one downloaded session's raw per-sample records "
            "(e.g. 20260116233312.vld) as CSV instead of generating a "
            "reading report. Ignores --format/--period/--from/--to; "
            "--output still applies (default: <FILENAME>-records.csv)"
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Optional argument list (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code.
    """
    args = _parse_args(argv)

    db_path = args.db
    report_config = DEFAULT_REPORT_CONFIG
    profile_config = DEFAULT_PROFILE_CONFIG
    if args.config:
        try:
            db_path = load_config(args.config).db_path
            report_config = load_report_config(args.config)
            profile_config = load_profile_config(args.config)
        except ConfigError as exc:
            print(f"Error: {exc}")
            return 1

    ensure_schema(db_path)

    if args.export_session:
        records = fetch_session_records(db_path, args.export_session, args.address)
        if not records:
            print(f"No session found matching filename {args.export_session!r}.")
            return 1
        output = args.output or f"{args.export_session}-records.csv"
        build_session_records_csv(records, output)
        print(f"Wrote {len(records)} record(s) to {output}")
        return 0

    start, end = _resolve_range(args.period, args.from_date, args.to_date)
    output = args.output or f"o2ring-report.{args.format}"

    rows = fetch_rows(db_path, args.address, start, end, report_config.exclude_not_worn)
    sessions = fetch_sessions(db_path, args.address, start, end)
    if not rows and not sessions:
        print("No readings or sessions found for the given range/filters.")
        return 1

    effective_report_config = _apply_profile_overrides(report_config, profile_config)

    if args.format == "csv":
        build_csv(rows, output, effective_report_config)
    else:
        build_pdf(rows, output, effective_report_config, profile_config, sessions)
    print(f"Wrote {len(rows)} reading(s) and {len(sessions)} session(s) to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
