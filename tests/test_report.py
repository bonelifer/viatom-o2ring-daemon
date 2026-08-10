import csv

from viatom_o2ring_daemon.config import DEFAULT_REPORT_CONFIG
from viatom_o2ring_daemon.report import build_csv, build_pdf, fetch_rows, fetch_sessions
from viatom_o2ring_daemon.storage import ReadingStore

_ADDRESS = "AA:BB:CC:DD:EE:FF"


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
