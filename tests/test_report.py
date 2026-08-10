import csv
import datetime
from dataclasses import replace

from viatom_o2ring_daemon.config import DEFAULT_PROFILE_CONFIG, DEFAULT_REPORT_CONFIG
from viatom_o2ring_daemon.report import (
    _apply_profile_overrides,
    build_csv,
    build_pdf,
    build_session_records_csv,
    fetch_rows,
    fetch_session_records,
    fetch_sessions,
    main,
)
from viatom_o2ring_daemon.storage import ReadingStore

_ADDRESS = "AA:BB:CC:DD:EE:FF"


class _FakeHeader:
    def __init__(self):
        self.start_time = datetime.datetime(2026, 1, 16, 23, 33, 12)
        self.mode = 1
        self.duration_seconds = 8
        self.spo2_avg = 96
        self.spo2_min = 90
        self.spo2_below_3pct_events = 0
        self.spo2_below_4pct_events = 0
        self.seconds_below_90pct = 0
        self.events_below_90pct = 0
        self.percent_below_90pct = 0.0
        self.o2_score = 9.0
        self.steps = 100
        self.record_count = 2
        self.resolution_seconds = 4.0


class _FakeRecord:
    def __init__(self, time, spo2, heart_rate, acceleration):
        self.time = time
        self.spo2 = spo2
        self.heart_rate = heart_rate
        self.acceleration = acceleration


def _seed_session(tmp_path, db_path):
    store = ReadingStore(db_path)
    header = _FakeHeader()
    records = [
        _FakeRecord(header.start_time, 96, 70, 1),
        _FakeRecord(header.start_time + datetime.timedelta(seconds=4), 94, 72, 2),
    ]
    store.record_session(
        _ADDRESS, "20260116233312.vld", "2026-01-17T08:00:00+00:00", header, records
    )
    store.close()


def _seed(tmp_path):
    db_path = str(tmp_path / "readings.db")
    store = ReadingStore(db_path)
    try:
        store.record_reading(
            recorded_at="2026-01-01T00:00:00+00:00", address=_ADDRESS, spo2=97,
            pulse_bpm=68, battery=80, battery_state=0, perfusion_index=8,
            worn=True, calibrating=False,
        )
        store.record_reading(
            recorded_at="2026-01-01T00:00:10+00:00", address=_ADDRESS, spo2=88,
            pulse_bpm=75, battery=80, battery_state=0, perfusion_index=6,
            worn=True, calibrating=False,
        )
        store.record_reading(
            recorded_at="2026-01-01T00:00:20+00:00", address=_ADDRESS, spo2=0,
            pulse_bpm=0, battery=80, battery_state=0, perfusion_index=0,
            worn=False, calibrating=False,
        )
    finally:
        store.close()
    return db_path


def test_fetch_rows_excludes_not_worn_by_default(tmp_path):
    db_path = _seed(tmp_path)
    rows = fetch_rows(db_path, None, None, None, exclude_not_worn=True)
    assert len(rows) == 2
    assert all(row.worn for row in rows)


def test_fetch_rows_can_include_not_worn(tmp_path):
    db_path = _seed(tmp_path)
    rows = fetch_rows(db_path, None, None, None, exclude_not_worn=False)
    assert len(rows) == 3


def test_fetch_sessions_empty(tmp_path):
    db_path = _seed(tmp_path)
    assert fetch_sessions(db_path, None, None, None) == []


def test_build_csv(tmp_path):
    db_path = _seed(tmp_path)
    rows = fetch_rows(db_path, None, None, None, exclude_not_worn=True)
    output = str(tmp_path / "out.csv")
    build_csv(rows, output, DEFAULT_REPORT_CONFIG)

    with open(output, newline="") as f:
        reader = list(csv.reader(f))
    assert reader[0][0] == "Date/Time (local)"
    assert len(reader) == 3  # header + 2 worn rows


def test_build_pdf(tmp_path):
    db_path = _seed(tmp_path)
    rows = fetch_rows(db_path, None, None, None, exclude_not_worn=True)
    output = str(tmp_path / "out.pdf")
    build_pdf(rows, output, DEFAULT_REPORT_CONFIG)
    assert (tmp_path / "out.pdf").stat().st_size > 0


def test_build_pdf_rollup_layout(tmp_path):
    from dataclasses import replace

    db_path = _seed(tmp_path)
    rows = fetch_rows(db_path, None, None, None, exclude_not_worn=True)
    output = str(tmp_path / "rollup.pdf")
    report_config = replace(DEFAULT_REPORT_CONFIG, table_layout="rollup")
    build_pdf(rows, output, report_config)
    assert (tmp_path / "rollup.pdf").stat().st_size > 0


def test_build_pdf_compact_layout(tmp_path):
    from dataclasses import replace

    db_path = _seed(tmp_path)
    rows = fetch_rows(db_path, None, None, None, exclude_not_worn=True)
    output = str(tmp_path / "compact.pdf")
    report_config = replace(DEFAULT_REPORT_CONFIG, table_layout="compact")
    build_pdf(rows, output, report_config)
    assert (tmp_path / "compact.pdf").stat().st_size > 0


def test_build_pdf_empty_rows(tmp_path):
    output = str(tmp_path / "empty.pdf")
    build_pdf([], output, DEFAULT_REPORT_CONFIG)
    assert (tmp_path / "empty.pdf").stat().st_size > 0


def test_fetch_session_records(tmp_path):
    db_path = _seed(tmp_path)
    _seed_session(tmp_path, db_path)

    records = fetch_session_records(db_path, "20260116233312.vld")
    assert len(records) == 2
    assert records[0].spo2 == 96
    assert records[1].spo2 == 94
    assert records[0].time < records[1].time


def test_fetch_session_records_unknown_filename(tmp_path):
    db_path = _seed(tmp_path)
    _seed_session(tmp_path, db_path)
    assert fetch_session_records(db_path, "nonexistent.vld") == []


def test_fetch_session_records_filters_by_address(tmp_path):
    db_path = _seed(tmp_path)
    _seed_session(tmp_path, db_path)
    assert fetch_session_records(db_path, "20260116233312.vld", address="00:00:00:00:00:00") == []
    assert len(fetch_session_records(db_path, "20260116233312.vld", address=_ADDRESS)) == 2


def test_build_session_records_csv(tmp_path):
    db_path = _seed(tmp_path)
    _seed_session(tmp_path, db_path)
    records = fetch_session_records(db_path, "20260116233312.vld")

    output = str(tmp_path / "records.csv")
    build_session_records_csv(records, output)

    with open(output, newline="") as f:
        reader = list(csv.reader(f))
    assert reader[0] == ["Time (device clock)", "SpO2 (%)", "Heart Rate (bpm)", "Acceleration"]
    assert len(reader) == 3  # header + 2 records
    assert reader[1][1] == "96"


def test_cli_export_session(tmp_path):
    db_path = _seed(tmp_path)
    _seed_session(tmp_path, db_path)
    output = str(tmp_path / "records.csv")

    exit_code = main(
        ["--db", db_path, "--export-session", "20260116233312.vld", "--output", output]
    )
    assert exit_code == 0

    with open(output, newline="") as f:
        reader = list(csv.reader(f))
    assert len(reader) == 3


def test_cli_export_session_unknown_filename(tmp_path):
    db_path = _seed(tmp_path)
    exit_code = main(["--db", db_path, "--export-session", "nonexistent.vld"])
    assert exit_code == 1


def test_apply_profile_overrides_no_profile():
    result = _apply_profile_overrides(DEFAULT_REPORT_CONFIG, DEFAULT_PROFILE_CONFIG)
    assert result == DEFAULT_REPORT_CONFIG


def test_apply_profile_overrides_region_us():
    base = replace(DEFAULT_REPORT_CONFIG, date_format="world", page_size="a4")
    profile = replace(DEFAULT_PROFILE_CONFIG, region="us")
    result = _apply_profile_overrides(base, profile)
    assert result.date_format == "us"
    assert result.page_size == "letter"


def test_apply_profile_overrides_region_world():
    base = replace(DEFAULT_REPORT_CONFIG, date_format="us", page_size="letter")
    profile = replace(DEFAULT_PROFILE_CONFIG, region="world")
    result = _apply_profile_overrides(base, profile)
    assert result.date_format == "world"
    assert result.page_size == "a4"


def test_apply_profile_overrides_explicit_field_wins_over_region():
    base = DEFAULT_REPORT_CONFIG
    profile = replace(DEFAULT_PROFILE_CONFIG, region="us", page_size="a4")
    result = _apply_profile_overrides(base, profile)
    assert result.date_format == "us"  # from region
    assert result.page_size == "a4"  # explicit override wins over region's "letter"
