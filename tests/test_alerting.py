from dataclasses import replace
from datetime import datetime, timezone

from viatom_o2ring_daemon.alerting import check_alerts
from viatom_o2ring_daemon.config import DEFAULT_ALERT_CONFIG
from viatom_o2ring_daemon.storage import ReadingStore

_ADDRESS = "AA:BB:CC:DD:EE:FF"


def _record(store, recorded_at, spo2=97, battery=80, worn=True):
    store.record_reading(
        recorded_at=recorded_at,
        address=_ADDRESS,
        spo2=spo2,
        pulse_bpm=68,
        battery=battery,
        battery_state=0,
        perfusion_index=8,
        worn=worn,
        calibrating=False,
    )


def test_no_alerts_when_disabled_checks(tmp_path):
    db_path = str(tmp_path / "readings.db")
    store = ReadingStore(db_path)
    _record(store, "2026-01-01T00:00:00+00:00")
    store.close()

    config = replace(DEFAULT_ALERT_CONFIG, state_path=str(tmp_path / "state.json"))
    alerts = check_alerts(db_path, config)
    assert alerts == []


def test_staleness_alert(tmp_path):
    db_path = str(tmp_path / "readings.db")
    store = ReadingStore(db_path)
    _record(store, "2026-01-01T00:00:00+00:00")
    store.close()

    config = replace(
        DEFAULT_ALERT_CONFIG,
        enabled=True,
        apprise_urls=["json://localhost"],
        stale_after_minutes=30,
        state_path=str(tmp_path / "state.json"),
    )
    now = datetime(2026, 1, 1, 1, 0, 0, tzinfo=timezone.utc)
    alerts = check_alerts(db_path, config, now=now)
    assert len(alerts) == 1
    assert "No reading" in alerts[0].message
    assert alerts[0].urls == ["json://localhost"]


def test_staleness_alert_throttled_on_repeat_check(tmp_path):
    db_path = str(tmp_path / "readings.db")
    store = ReadingStore(db_path)
    _record(store, "2026-01-01T00:00:00+00:00")
    store.close()

    config = replace(
        DEFAULT_ALERT_CONFIG,
        enabled=True,
        apprise_urls=["json://localhost"],
        stale_after_minutes=30,
        state_path=str(tmp_path / "state.json"),
    )
    now = datetime(2026, 1, 1, 1, 0, 0, tzinfo=timezone.utc)
    first = check_alerts(db_path, config, now=now)
    assert len(first) == 1

    ten_minutes_later = datetime(2026, 1, 1, 1, 10, 0, tzinfo=timezone.utc)
    second = check_alerts(db_path, config, now=ten_minutes_later)
    assert second == []


def test_low_spo2_alert_fires_once(tmp_path):
    db_path = str(tmp_path / "readings.db")
    store = ReadingStore(db_path)
    _record(store, "2026-01-01T00:00:00+00:00", spo2=85)
    store.close()

    config = replace(
        DEFAULT_ALERT_CONFIG,
        enabled=True,
        apprise_urls=["json://localhost"],
        low_spo2_percent=90,
        state_path=str(tmp_path / "state.json"),
    )
    now = datetime(2026, 1, 1, 0, 1, 0, tzinfo=timezone.utc)
    first = check_alerts(db_path, config, now=now)
    assert len(first) == 1
    assert "Low SpO2" in first[0].message

    second = check_alerts(db_path, config, now=now)
    assert second == []


def test_low_spo2_alert_ignored_when_not_worn(tmp_path):
    db_path = str(tmp_path / "readings.db")
    store = ReadingStore(db_path)
    _record(store, "2026-01-01T00:00:00+00:00", spo2=85, worn=False)
    store.close()

    config = replace(
        DEFAULT_ALERT_CONFIG,
        enabled=True,
        apprise_urls=["json://localhost"],
        low_spo2_percent=90,
        state_path=str(tmp_path / "state.json"),
    )
    alerts = check_alerts(db_path, config)
    assert alerts == []


def test_low_battery_alert(tmp_path):
    db_path = str(tmp_path / "readings.db")
    store = ReadingStore(db_path)
    _record(store, "2026-01-01T00:00:00+00:00", battery=5)
    store.close()

    config = replace(
        DEFAULT_ALERT_CONFIG,
        enabled=True,
        apprise_urls=["json://localhost"],
        low_battery_percent=10,
        state_path=str(tmp_path / "state.json"),
    )
    alerts = check_alerts(db_path, config)
    assert len(alerts) == 1
    assert "Low battery" in alerts[0].message
