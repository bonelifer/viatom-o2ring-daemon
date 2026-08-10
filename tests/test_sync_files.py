import asyncio
import datetime

from viatom_o2ring_daemon import sync_files as sync_files_module
from viatom_o2ring_daemon.storage import get_synced_filenames

_ADDRESS = "AA:BB:CC:DD:EE:FF"


class _FakeInfo:
    def __init__(self, file_names):
        self.file_names = file_names


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
    def __init__(self, time):
        self.time = time
        self.spo2 = 96
        self.heart_rate = 70
        self.acceleration = 0


class _FakeClient:
    def __init__(self, address, **kwargs):
        self.address = address
        self.connected = False

    async def async_connect(self, timeout=None):
        self.connected = True

    async def async_disconnect(self):
        self.connected = False

    async def get_info(self):
        return _FakeInfo(["20260116233312.vld", "bad.vld"])

    async def download_and_parse_file(self, filename):
        if filename == "bad.vld":
            raise ValueError("corrupt file")
        header = _FakeHeader()
        records = [
            _FakeRecord(header.start_time),
            _FakeRecord(header.start_time + datetime.timedelta(seconds=4)),
        ]
        return header, records


def test_sync_files_downloads_new_and_skips_bad(tmp_path, monkeypatch):
    monkeypatch.setattr(sync_files_module, "O2RingClient", _FakeClient)
    db_path = str(tmp_path / "readings.db")

    downloaded = asyncio.run(sync_files_module.sync_files(db_path, _ADDRESS))
    assert downloaded == 1
    assert get_synced_filenames(db_path, _ADDRESS) == {"20260116233312.vld"}


def test_sync_files_skips_already_synced(tmp_path, monkeypatch):
    monkeypatch.setattr(sync_files_module, "O2RingClient", _FakeClient)
    db_path = str(tmp_path / "readings.db")

    asyncio.run(sync_files_module.sync_files(db_path, _ADDRESS))
    downloaded_again = asyncio.run(sync_files_module.sync_files(db_path, _ADDRESS))
    assert downloaded_again == 0
