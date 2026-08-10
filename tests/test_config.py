from pathlib import Path

import pytest

from viatom_o2ring_daemon.config import (
    ConfigError,
    load_alert_config,
    load_api_config,
    load_config,
    load_file_sync_config,
    load_mqtt_config,
    load_profile_config,
    load_report_config,
    persist_discovered_address,
)

_BASE_CONFIG = """
[monitor]
address = AA:BB:CC:DD:EE:FF
adapter =
cooldown_seconds = 5
read_period = 2.0

[storage]
db_path = /tmp/readings.db

[daemon]
log_level = INFO
"""


def _write(tmp_path: Path, contents: str) -> str:
    path = tmp_path / "config.ini"
    path.write_text(contents)
    return str(path)


def test_load_config_basic(tmp_path):
    config_path = _write(tmp_path, _BASE_CONFIG)
    config = load_config(config_path)
    assert config.address == "AA:BB:CC:DD:EE:FF"
    assert config.cooldown_seconds == 5
    assert config.read_period == 2.0
    assert config.legacy_sensors is False
    assert config.db_path == "/tmp/readings.db"
    assert config.log_level == "INFO"


def test_load_config_missing_file():
    with pytest.raises(ConfigError):
        load_config("/nonexistent/config.ini")


def test_load_config_requires_db_path(tmp_path):
    config_path = _write(tmp_path, "[storage]\ndb_path =\n")
    with pytest.raises(ConfigError):
        load_config(config_path)


def test_load_config_legacy_sensors(tmp_path):
    contents = _BASE_CONFIG.replace(
        "cooldown_seconds = 5", "cooldown_seconds = 5\nlegacy_sensors = yes"
    )
    config_path = _write(tmp_path, contents)
    config = load_config(config_path)
    assert config.legacy_sensors is True


def test_load_report_config_defaults(tmp_path):
    config_path = _write(tmp_path, _BASE_CONFIG)
    report = load_report_config(config_path)
    assert report.include_categories is True
    assert report.include_chart is True
    assert report.include_table is True
    assert report.table_layout == "full"
    assert report.rollup_period == "week"
    assert report.exclude_not_worn is True


def test_load_report_config_invalid_date_format(tmp_path):
    config_path = _write(tmp_path, _BASE_CONFIG + "\n[report]\ndate_format = martian\n")
    with pytest.raises(ConfigError):
        load_report_config(config_path)


def test_load_report_config_table_layout(tmp_path):
    config_path = _write(
        tmp_path,
        _BASE_CONFIG + "\n[report]\ntable_layout = rollup\nrollup_period = month\n",
    )
    report = load_report_config(config_path)
    assert report.table_layout == "rollup"
    assert report.rollup_period == "month"


def test_load_profile_config_missing_section(tmp_path):
    config_path = _write(tmp_path, _BASE_CONFIG)
    profile = load_profile_config(config_path)
    assert profile.name == ""
    assert profile.stale_after_minutes is None


def test_load_profile_config(tmp_path):
    config_path = _write(
        tmp_path,
        _BASE_CONFIG
        + "\n[profile]\nname = Jane\nemail = jane@example.com\nlow_spo2_percent = 90\n"
        "apprise_urls = json://localhost\n",
    )
    profile = load_profile_config(config_path)
    assert profile.name == "Jane"
    assert profile.email == "jane@example.com"
    assert profile.low_spo2_percent == 90
    assert profile.apprise_urls == ["json://localhost"]


def test_load_profile_config_percent_in_notes_does_not_crash(tmp_path):
    # configparser treats "%" as interpolation syntax by default, which
    # would break on a very plausible O2Ring notes value like this one --
    # see config.py's ConfigParser(interpolation=None).
    config_path = _write(
        tmp_path,
        _BASE_CONFIG + "\n[profile]\nnotes = target SpO2 >= 92%\n",
    )
    profile = load_profile_config(config_path)
    assert profile.notes == "target SpO2 >= 92%"


def test_load_mqtt_config_disabled_by_default(tmp_path):
    config_path = _write(tmp_path, _BASE_CONFIG)
    mqtt = load_mqtt_config(config_path)
    assert mqtt.enabled is False


def test_load_mqtt_config_requires_host_when_enabled(tmp_path):
    config_path = _write(tmp_path, _BASE_CONFIG + "\n[mqtt]\nenabled = yes\n")
    with pytest.raises(ConfigError):
        load_mqtt_config(config_path)


def test_load_alert_config_requires_urls_when_enabled(tmp_path):
    config_path = _write(tmp_path, _BASE_CONFIG + "\n[alerting]\nenabled = yes\n")
    with pytest.raises(ConfigError):
        load_alert_config(config_path)


def test_load_alert_config_requires_a_check(tmp_path):
    config_path = _write(
        tmp_path,
        _BASE_CONFIG + "\n[alerting]\nenabled = yes\napprise_urls = json://localhost\n",
    )
    with pytest.raises(ConfigError):
        load_alert_config(config_path)


def test_load_alert_config_valid(tmp_path):
    config_path = _write(
        tmp_path,
        _BASE_CONFIG
        + "\n[alerting]\nenabled = yes\napprise_urls = json://localhost\n"
        "low_spo2_percent = 88\n",
    )
    alert = load_alert_config(config_path)
    assert alert.enabled is True
    assert alert.low_spo2_percent == 88


def test_load_api_config_defaults(tmp_path):
    config_path = _write(tmp_path, _BASE_CONFIG)
    api = load_api_config(config_path)
    assert api.enabled is False
    assert api.host == "127.0.0.1"
    assert api.port == 8080


def test_load_file_sync_config_defaults_enabled(tmp_path):
    config_path = _write(tmp_path, _BASE_CONFIG)
    file_sync = load_file_sync_config(config_path)
    assert file_sync.enabled is True


def test_load_file_sync_config_disabled(tmp_path):
    config_path = _write(tmp_path, _BASE_CONFIG + "\n[file_sync]\nenabled = no\n")
    file_sync = load_file_sync_config(config_path)
    assert file_sync.enabled is False


def test_persist_discovered_address(tmp_path):
    config_path = tmp_path / "config.ini"
    config_path.write_text(_BASE_CONFIG.replace("AA:BB:CC:DD:EE:FF", ""))
    persist_discovered_address(config_path, "11:22:33:44:55:66")
    config = load_config(str(config_path))
    assert config.address == "11:22:33:44:55:66"
