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


class _FakeOxyIIEntry:
    def __init__(self, name):
        self.name = name


class _FakeOxyIIHeader:
    def __init__(self, record_count=2, trailer_confirmed=True):
        self.record_count = record_count
        self.finalized = trailer_confirmed
        self.trailer_confirmed = trailer_confirmed
        self.spo2_avg = 95
        self.spo2_min = 90
        self.spo2_below_3pct_events = 1
        self.spo2_below_4pct_events = 0
        self.seconds_below_90pct = 0
        self.events_below_90pct = 0
        self.o2_score = 8.5
        self.heart_rate_avg = 70


class _FakeOxyIIRecord:
    def __init__(self, index, spo2=95, heart_rate=70):
        self.index = index
        self.spo2 = spo2
        self.heart_rate = heart_rate
        self.status_flags = 0


class _FakeOxyIIClient:
    def __init__(self, address, **kwargs):
        self.address = address

    async def async_connect(self, timeout=None):
        pass

    async def async_disconnect(self):
        pass

    async def get_file_list(self):
        return [
            _FakeOxyIIEntry("20260116233312"),  # finalized
            _FakeOxyIIEntry("20260117080000"),  # not yet finalized
            _FakeOxyIIEntry("bad"),  # raises on download
        ]

    async def download_and_parse_file(self, filename):
        if filename == "bad":
            raise ValueError("corrupt file")
        if filename == "20260117080000":
            header = _FakeOxyIIHeader(trailer_confirmed=False)
            return header, [_FakeOxyIIRecord(0), _FakeOxyIIRecord(1)]
        header = _FakeOxyIIHeader()
        return header, [_FakeOxyIIRecord(0), _FakeOxyIIRecord(1)]


def test_sync_files_oxyii_stores_finalized_skips_unfinalized_and_bad(tmp_path, monkeypatch):
    monkeypatch.setattr(sync_files_module, "OxyIIClient", _FakeOxyIIClient)
    db_path = str(tmp_path / "readings.db")

    downloaded = asyncio.run(sync_files_module.sync_files(db_path, _ADDRESS, protocol="oxyii"))
    assert downloaded == 1
    assert get_synced_filenames(db_path, _ADDRESS) == {"20260116233312"}


def test_sync_files_oxyii_unfinalized_file_is_retried_later(tmp_path, monkeypatch):
    monkeypatch.setattr(sync_files_module, "OxyIIClient", _FakeOxyIIClient)
    db_path = str(tmp_path / "readings.db")

    asyncio.run(sync_files_module.sync_files(db_path, _ADDRESS, protocol="oxyii"))
    # The unfinalized file was never stored, so it's still "pending" on a
    # second sync -- confirmed indirectly: it's absent from synced names.
    assert "20260117080000" not in get_synced_filenames(db_path, _ADDRESS)


def test_adapt_oxyii_session_derives_times_from_filename_and_index():
    header = _FakeOxyIIHeader(record_count=2)
    records = [
        _FakeOxyIIRecord(0, spo2=97, heart_rate=68),
        _FakeOxyIIRecord(1, spo2=96, heart_rate=70),
    ]

    adapted_header, adapted_records = sync_files_module._adapt_oxyii_session(
        "20260116233312", header, records
    )

    assert adapted_header.start_time == datetime.datetime(2026, 1, 16, 23, 33, 12)
    assert adapted_header.duration_seconds == 2
    assert adapted_header.resolution_seconds == 1.0
    assert adapted_header.spo2_avg == 95

    assert adapted_records[0].time == datetime.datetime(2026, 1, 16, 23, 33, 12)
    assert adapted_records[1].time == datetime.datetime(2026, 1, 16, 23, 33, 13)
    assert adapted_records[0].spo2 == 97
    assert adapted_records[0].heart_rate == 68
