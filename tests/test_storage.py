import datetime

from viatom_o2ring_daemon.storage import ReadingStore, get_synced_filenames

_ADDRESS = "AA:BB:CC:DD:EE:FF"


class _FakeHeader:
    def __init__(self):
        self.start_time = datetime.datetime(2026, 1, 16, 23, 33, 12)
        self.mode = 1
        self.duration_seconds = 28800
        self.spo2_avg = 96
        self.spo2_min = 88
        self.spo2_below_3pct_events = 3
        self.spo2_below_4pct_events = 1
        self.seconds_below_90pct = 120
        self.events_below_90pct = 2
        self.percent_below_90pct = 0.5
        self.o2_score = 8.5
        self.steps = 4200
        self.record_count = 2
        self.resolution_seconds = 4.0


class _FakeRecord:
    def __init__(self, time, spo2, heart_rate, acceleration):
        self.time = time
        self.spo2 = spo2
        self.heart_rate = heart_rate
        self.acceleration = acceleration


def test_record_reading(tmp_path):
    db_path = str(tmp_path / "readings.db")
    store = ReadingStore(db_path)
    try:
        row_id = store.record_reading(
            recorded_at="2026-01-01T00:00:00+00:00",
            address=_ADDRESS,
            spo2=97,
            pulse_bpm=68,
            battery=80,
            battery_state=0,
            perfusion_index=8,
            worn=True,
            calibrating=False,
        )
        assert row_id == 1
    finally:
        store.close()


def test_record_session_and_dedup(tmp_path):
    db_path = str(tmp_path / "readings.db")
    store = ReadingStore(db_path)
    header = _FakeHeader()
    records = [
        _FakeRecord(header.start_time, 96, 70, 1),
        _FakeRecord(header.start_time + datetime.timedelta(seconds=4), 95, 71, 1),
    ]
    try:
        session_id = store.record_session(
            _ADDRESS, "20260116233312.vld", "2026-01-17T08:00:00+00:00", header, records
        )
        assert session_id == 1

        # Re-downloading the same file is a no-op, not a duplicate row.
        again = store.record_session(
            _ADDRESS, "20260116233312.vld", "2026-01-17T09:00:00+00:00", header, records
        )
        assert again is None
    finally:
        store.close()

    assert get_synced_filenames(db_path, _ADDRESS) == {"20260116233312.vld"}


def test_get_synced_filenames_no_table(tmp_path):
    db_path = str(tmp_path / "empty.db")
    assert get_synced_filenames(db_path, _ADDRESS) == set()
