from datetime import datetime, timezone

from viatom_o2ring_ble import Reading, RtReading

from viatom_o2ring_daemon.cli import _check_config, _reading_to_row

_ADDRESS = "AA:BB:CC:DD:EE:FF"

_BASE_CONFIG = f"""
[monitor]
address = {_ADDRESS}

[storage]
db_path = {{db_path}}

[daemon]
log_level = INFO
"""


def test_reading_to_row_rt_reading():
    reading = RtReading(
        spo2=97, pulse_bpm=68, battery=80, battery_state=0, perfusion_index=8,
        worn=True, calibrating=False, waveform=b"", raw=b"",
        received_at=datetime.now(timezone.utc),
    )
    row = _reading_to_row(reading, _ADDRESS)
    assert row["spo2"] == 97
    assert row["pulse_bpm"] == 68
    assert row["battery_state"] == 0
    assert row["worn"] is True


def test_reading_to_row_legacy_reading():
    reading = Reading(
        spo2=95, heart_rate=72, battery=60, charging=1, movement=0,
        perfusion_index=5, worn=True, calibrating=False, raw=b"",
        received_at=datetime.now(timezone.utc),
    )
    row = _reading_to_row(reading, _ADDRESS)
    assert row["spo2"] == 95
    assert row["pulse_bpm"] == 72
    assert row["battery_state"] == 1


def test_check_config_valid(tmp_path, capsys):
    config_path = tmp_path / "config.ini"
    config_path.write_text(_BASE_CONFIG.format(db_path=tmp_path / "readings.db"))
    assert _check_config(str(config_path)) == 0
    assert "OK" in capsys.readouterr().out


def test_check_config_missing_file(capsys):
    assert _check_config("/nonexistent/config.ini") == 1
    assert "not found" in capsys.readouterr().out


def test_check_config_invalid(tmp_path, capsys):
    config_path = tmp_path / "config.ini"
    config_path.write_text("[storage]\ndb_path =\n")
    assert _check_config(str(config_path)) == 1
    assert "INVALID" in capsys.readouterr().out
