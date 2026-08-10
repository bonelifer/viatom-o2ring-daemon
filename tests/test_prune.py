import datetime

from viatom_o2ring_daemon.prune import count_old_rows, delete_old_rows
from viatom_o2ring_daemon.storage import ReadingStore

_ADDRESS = "AA:BB:CC:DD:EE:FF"


class _FakeHeader:
    def __init__(self, start_time):
        self.start_time = start_time
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
    def __init__(self, time):
        self.time = time
        self.spo2 = 96
        self.heart_rate = 70
        self.acceleration = 0


def _seed(tmp_path):
    db_path = str(tmp_path / "readings.db")
    store = ReadingStore(db_path)
    store.record_reading(
        recorded_at="2020-01-01T00:00:00+00:00", address=_ADDRESS, spo2=97,
        pulse_bpm=68, battery=80, battery_state=0, perfusion_index=8,
        worn=True, calibrating=False,
    )
    store.record_reading(
        recorded_at="2026-01-01T00:00:00+00:00", address=_ADDRESS, spo2=97,
        pulse_bpm=68, battery=80, battery_state=0, perfusion_index=8,
        worn=True, calibrating=False,
    )
    old_start = datetime.datetime(2020, 1, 1, 0, 0, 0)
    header = _FakeHeader(old_start)
    records = [_FakeRecord(old_start), _FakeRecord(old_start + datetime.timedelta(seconds=4))]
    store.record_session(_ADDRESS, "old.vld", "2020-01-01T00:00:00+00:00", header, records)
    store.close()
    return db_path


def test_count_old_rows(tmp_path):
    db_path = _seed(tmp_path)
    cutoff = datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc)
    readings, sessions = count_old_rows(db_path, cutoff, None)
    assert readings == 1
    assert sessions == 1


def test_delete_old_rows(tmp_path):
    db_path = _seed(tmp_path)
    cutoff = datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc)
    readings, sessions = delete_old_rows(db_path, cutoff, None)
    assert readings == 1
    assert sessions == 1

    remaining_readings, remaining_sessions = count_old_rows(
        db_path, datetime.datetime(2030, 1, 1, tzinfo=datetime.timezone.utc), None
    )
    assert remaining_readings == 1  # the 2026 reading is still there
    assert remaining_sessions == 0
