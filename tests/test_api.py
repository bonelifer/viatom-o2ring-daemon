import asyncio
import datetime
from dataclasses import replace

from aiohttp.test_utils import TestClient, TestServer

from viatom_o2ring_daemon.api import build_app, is_insecurely_exposed
from viatom_o2ring_daemon.config import (
    DEFAULT_API_CONFIG,
    DEFAULT_PROFILE_CONFIG,
    DEFAULT_REPORT_CONFIG,
)
from viatom_o2ring_daemon.storage import ReadingStore, ensure_schema

_ADDRESS = "AA:BB:CC:DD:EE:FF"


def _run(coro):
    return asyncio.run(coro)


def _make_db(tmp_path):
    db_path = str(tmp_path / "readings.db")
    store = ReadingStore(db_path)
    store.record_reading(
        recorded_at="2026-01-01T00:00:00+00:00", address=_ADDRESS, spo2=97,
        pulse_bpm=68, battery=80, battery_state=0, perfusion_index=8,
        worn=True, calibrating=False,
    )
    store.close()
    return db_path


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


def _make_db_with_session(tmp_path):
    db_path = _make_db(tmp_path)
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
    return db_path


def _build(db_path, api_config=DEFAULT_API_CONFIG, report_config=DEFAULT_REPORT_CONFIG,
           profile_config=DEFAULT_PROFILE_CONFIG):
    return build_app(db_path, api_config, report_config, profile_config)


def test_is_insecurely_exposed():
    assert is_insecurely_exposed(replace(DEFAULT_API_CONFIG, host="0.0.0.0")) is True
    assert is_insecurely_exposed(replace(DEFAULT_API_CONFIG, host="0.0.0.0", token="x")) is False
    assert is_insecurely_exposed(DEFAULT_API_CONFIG) is False


def test_health(tmp_path):
    app = _build(_make_db(tmp_path))

    async def scenario():
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/health")
            assert resp.status == 200
            body = await resp.json()
            assert body["status"] == "ok"

    _run(scenario())


def test_latest(tmp_path):
    app = _build(_make_db(tmp_path))

    async def scenario():
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/latest")
            assert resp.status == 200
            body = await resp.json()
            assert body["spo2"] == 97

    _run(scenario())


def test_latest_no_data(tmp_path):
    db_path = str(tmp_path / "empty.db")
    ensure_schema(db_path)
    app = _build(db_path)

    async def scenario():
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/latest")
            assert resp.status == 404

    _run(scenario())


def test_sessions_empty(tmp_path):
    app = _build(_make_db(tmp_path))

    async def scenario():
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/sessions")
            assert resp.status == 200
            assert await resp.json() == []

    _run(scenario())


def test_report_pdf(tmp_path):
    app = _build(_make_db(tmp_path))

    async def scenario():
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/report?format=pdf")
            assert resp.status == 200
            assert resp.content_type == "application/pdf"

    _run(scenario())


def test_report_invalid_format(tmp_path):
    app = _build(_make_db(tmp_path))

    async def scenario():
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/report?format=xml")
            assert resp.status == 400

    _run(scenario())


def test_report_no_data(tmp_path):
    db_path = str(tmp_path / "empty.db")
    ensure_schema(db_path)
    app = _build(db_path)

    async def scenario():
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/report?format=pdf")
            assert resp.status == 404

    _run(scenario())


def test_session_records_requires_filename(tmp_path):
    app = _build(_make_db(tmp_path))

    async def scenario():
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/session-records")
            assert resp.status == 400

    _run(scenario())


def test_session_records_unknown_filename(tmp_path):
    app = _build(_make_db(tmp_path))

    async def scenario():
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/session-records?filename=nonexistent.vld")
            assert resp.status == 404

    _run(scenario())


def test_session_records_json(tmp_path):
    app = _build(_make_db_with_session(tmp_path))

    async def scenario():
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/session-records?filename=20260116233312.vld")
            assert resp.status == 200
            body = await resp.json()
            assert len(body) == 2
            assert body[0]["spo2"] == 96

    _run(scenario())


def test_session_records_csv(tmp_path):
    app = _build(_make_db_with_session(tmp_path))

    async def scenario():
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/session-records?filename=20260116233312.vld&format=csv")
            assert resp.status == 200
            assert resp.content_type == "text/csv"
            body = await resp.text()
            assert "SpO2 (%)" in body

    _run(scenario())


def test_health_requires_no_auth_but_latest_does(tmp_path):
    api_config = replace(DEFAULT_API_CONFIG, token="secret")
    app = _build(_make_db(tmp_path), api_config=api_config)

    async def scenario():
        async with TestClient(TestServer(app)) as client:
            health = await client.get("/health")
            assert health.status == 200

            unauthorized = await client.get("/latest")
            assert unauthorized.status == 401

            authorized = await client.get("/latest", headers={"Authorization": "Bearer secret"})
            assert authorized.status == 200

    _run(scenario())
