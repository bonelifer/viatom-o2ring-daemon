"""Configuration loading and persistence for the daemon."""

from __future__ import annotations

import configparser
from dataclasses import dataclass
from pathlib import Path


class ConfigError(Exception):
    """Raised when the configuration file is missing or invalid."""


@dataclass
class DaemonConfig:
    """Parsed daemon configuration."""

    config_path: Path
    address: str
    adapter: str
    cooldown_seconds: int
    read_period: float
    legacy_sensors: bool
    db_path: str
    log_level: str


@dataclass
class ReportConfig:
    """Parsed [report] section controlling PDF/CSV report rendering."""

    include_address: bool
    include_summary: bool
    include_categories: bool
    include_sessions: bool
    include_chart: bool  # PDF only
    include_table: bool  # PDF only; CSV always exports the full row set
    table_layout: str  # "full", "compact", or "rollup" -- PDF only
    rollup_period: str  # "week" or "month" -- only used when table_layout = rollup
    date_format: str  # "us" or "world"
    page_size: str  # "letter" or "a4"
    exclude_not_worn: bool  # skip readings where worn = no


DEFAULT_REPORT_CONFIG = ReportConfig(
    include_address=True,
    include_summary=True,
    include_categories=True,
    include_sessions=True,
    include_chart=True,
    include_table=True,
    table_layout="full",
    rollup_period="week",
    date_format="world",
    page_size="letter",
    exclude_not_worn=True,
)

_DATE_FORMATS = ("us", "world")
_PAGE_SIZES = ("letter", "a4")
_TABLE_LAYOUTS = ("full", "compact", "rollup")
_ROLLUP_PERIODS = ("week", "month")


@dataclass
class ProfileConfig:
    """The ring wearer's identifying info, report preferences, and alert overrides.

    Unlike etekcity-bp-daemon/etekcity-scale-daemon, a single daemon
    instance already maps to exactly one device (`DaemonConfig.address`),
    and a ring is a single-wearer device by nature -- there's no "who was
    this?" tagging problem to solve, so there's exactly one optional
    ``[profile]`` section instead of a `[profiles]` list plus
    `[profile.<name>]` per person. A household with multiple rings runs
    multiple daemon instances (separate config files/db paths), same as
    they'd use separate addresses today.
    """

    name: str
    email: str
    notes: str
    region: str  # "" (unset), "us" (-> date_format=us, page_size=letter), or "world" (-> world, a4)
    date_format: str  # "" (unset, use region or report.date_format), "us", or "world"
    page_size: str  # "" (unset, use region or report.page_size), "letter", or "a4"
    apprise_urls: list[str]  # empty means "use [alerting] apprise_urls"
    stale_after_minutes: int | None  # None means "use [alerting] stale_after_minutes"
    low_spo2_percent: int | None  # None means "use [alerting] low_spo2_percent"


DEFAULT_PROFILE_CONFIG = ProfileConfig(
    name="",
    email="",
    notes="",
    region="",
    date_format="",
    page_size="",
    apprise_urls=[],
    stale_after_minutes=None,
    low_spo2_percent=None,
)

_REGIONS = ("us", "world")

# region -> (date_format, page_size) it implies, applied by
# report._apply_profile_overrides before that profile's own explicit
# date_format/page_size (which still take precedence if also set).
REGION_REPORT_DEFAULTS = {
    "us": ("us", "letter"),
    "world": ("world", "a4"),
}


@dataclass
class MqttConfig:
    """Parsed [mqtt] section: optional MQTT publishing of live readings."""

    enabled: bool
    host: str
    port: int
    username: str
    password: str
    use_tls: bool
    topic_prefix: str
    qos: int
    retain: bool


DEFAULT_MQTT_CONFIG = MqttConfig(
    enabled=False,
    host="",
    port=1883,
    username="",
    password="",
    use_tls=False,
    topic_prefix="viatom_o2ring_daemon",
    qos=0,
    retain=True,
)

_QOS_LEVELS = (0, 1, 2)


@dataclass
class AlertConfig:
    """Parsed [alerting] section: optional Apprise-based notifications."""

    enabled: bool
    apprise_urls: list[str]
    stale_after_minutes: int  # 0 disables the staleness check
    low_spo2_percent: int  # 0 disables the low-SpO2 check
    low_battery_percent: int  # 0 disables the low-battery check
    state_path: str


DEFAULT_ALERT_CONFIG = AlertConfig(
    enabled=False,
    apprise_urls=[],
    stale_after_minutes=0,
    low_spo2_percent=0,
    low_battery_percent=0,
    state_path="/var/lib/viatom-o2ring-daemon/alert-state.json",
)


@dataclass
class ApiConfig:
    """Parsed [api] section: optional local HTTP API for reading data on demand."""

    enabled: bool
    host: str
    port: int
    token: str  # "" means no authentication required


DEFAULT_API_CONFIG = ApiConfig(enabled=False, host="127.0.0.1", port=8080, token="")


@dataclass
class FileSyncConfig:
    """Parsed [file_sync] section: downloading stored .vld session files."""

    enabled: bool


DEFAULT_FILE_SYNC_CONFIG = FileSyncConfig(enabled=True)


def _parse_bool(value: str, key: str) -> bool:
    """Parse a yes/no-style config value.

    Args:
        value: Raw string from the config file.
        key: Dotted key name, used in the error message.

    Returns:
        The parsed boolean.

    Raises:
        ConfigError: If ``value`` isn't a recognized yes/no spelling.
    """
    normalized = value.strip().lower()
    if normalized in ("yes", "true", "1", "on"):
        return True
    if normalized in ("no", "false", "0", "off"):
        return False
    raise ConfigError(f"{key} must be yes/no, got {value!r}")


def load_config(config_path: str) -> DaemonConfig:
    """Load and validate the daemon configuration file.

    Args:
        config_path: Path to the INI configuration file.

    Returns:
        The parsed configuration.

    Raises:
        ConfigError: If the file is missing or a required value is invalid.
    """
    path = Path(config_path)
    if not path.is_file():
        raise ConfigError(
            f"Config file not found: {path}. Copy "
            "config/viatom-o2ring-daemon.ini.example to this path and edit it."
        )

    parser = configparser.ConfigParser(interpolation=None)
    parser.read(path)

    monitor = parser["monitor"] if parser.has_section("monitor") else {}
    storage = parser["storage"] if parser.has_section("storage") else {}
    daemon = parser["daemon"] if parser.has_section("daemon") else {}

    try:
        cooldown_seconds = int(monitor.get("cooldown_seconds", "5"))
    except ValueError as exc:
        raise ConfigError("monitor.cooldown_seconds must be an integer") from exc

    try:
        read_period = float(monitor.get("read_period", "2.0"))
    except ValueError as exc:
        raise ConfigError("monitor.read_period must be a number") from exc
    if read_period <= 0:
        raise ConfigError("monitor.read_period must be positive")

    db_path = storage.get("db_path", "").strip()
    if not db_path:
        raise ConfigError("storage.db_path must be set")

    return DaemonConfig(
        config_path=path,
        address=monitor.get("address", "").strip(),
        adapter=monitor.get("adapter", "").strip(),
        cooldown_seconds=cooldown_seconds,
        read_period=read_period,
        legacy_sensors=_parse_bool(
            monitor.get("legacy_sensors", "no"), "monitor.legacy_sensors"
        ),
        db_path=db_path,
        log_level=daemon.get("log_level", "INFO").strip().upper(),
    )


def load_report_config(config_path: str) -> ReportConfig:
    """Load the ``[report]`` section of the daemon config file, if present.

    Args:
        config_path: Path to the INI configuration file.

    Returns:
        The parsed report configuration, or ``DEFAULT_REPORT_CONFIG`` if the
        file has no ``[report]`` section.

    Raises:
        ConfigError: If the file is missing or a ``[report]`` value is invalid.
    """
    path = Path(config_path)
    if not path.is_file():
        raise ConfigError(f"Config file not found: {path}")

    parser = configparser.ConfigParser(interpolation=None)
    parser.read(path)

    if not parser.has_section("report"):
        return DEFAULT_REPORT_CONFIG

    report = parser["report"]

    date_format = report.get("date_format", DEFAULT_REPORT_CONFIG.date_format).strip().lower()
    if date_format not in _DATE_FORMATS:
        raise ConfigError(
            f"report.date_format must be one of {_DATE_FORMATS}, got {date_format!r}"
        )

    page_size = report.get("page_size", DEFAULT_REPORT_CONFIG.page_size).strip().lower()
    if page_size not in _PAGE_SIZES:
        raise ConfigError(f"report.page_size must be one of {_PAGE_SIZES}, got {page_size!r}")

    table_layout = report.get("table_layout", DEFAULT_REPORT_CONFIG.table_layout).strip().lower()
    if table_layout not in _TABLE_LAYOUTS:
        raise ConfigError(
            f"report.table_layout must be one of {_TABLE_LAYOUTS}, got {table_layout!r}"
        )

    rollup_period = report.get(
        "rollup_period", DEFAULT_REPORT_CONFIG.rollup_period
    ).strip().lower()
    if rollup_period not in _ROLLUP_PERIODS:
        raise ConfigError(
            f"report.rollup_period must be one of {_ROLLUP_PERIODS}, got {rollup_period!r}"
        )

    return ReportConfig(
        include_address=_parse_bool(
            report.get("include_address", "yes"), "report.include_address"
        ),
        include_summary=_parse_bool(
            report.get("include_summary", "yes"), "report.include_summary"
        ),
        include_categories=_parse_bool(
            report.get("include_categories", "yes"), "report.include_categories"
        ),
        include_sessions=_parse_bool(
            report.get("include_sessions", "yes"), "report.include_sessions"
        ),
        include_chart=_parse_bool(report.get("include_chart", "yes"), "report.include_chart"),
        include_table=_parse_bool(report.get("include_table", "yes"), "report.include_table"),
        table_layout=table_layout,
        rollup_period=rollup_period,
        date_format=date_format,
        page_size=page_size,
        exclude_not_worn=_parse_bool(
            report.get("exclude_not_worn", "yes"), "report.exclude_not_worn"
        ),
    )


def load_profile_config(config_path: str) -> ProfileConfig:
    """Load the ``[profile]`` section: identity, report prefs, alert overrides.

    A missing section just falls back to blanks/unset, since none of these
    fields are required for the daemon to function; they only personalize
    reports/alerts if provided.

    Args:
        config_path: Path to the INI configuration file.

    Returns:
        The parsed profile configuration, or ``DEFAULT_PROFILE_CONFIG`` if
        the file has no ``[profile]`` section.

    Raises:
        ConfigError: If the file is missing or a value is invalid.
    """
    path = Path(config_path)
    if not path.is_file():
        raise ConfigError(f"Config file not found: {path}")

    parser = configparser.ConfigParser(interpolation=None)
    parser.read(path)

    if not parser.has_section("profile"):
        return DEFAULT_PROFILE_CONFIG

    section = parser["profile"]

    region = section.get("region", "").strip().lower()
    if region and region not in _REGIONS:
        raise ConfigError(f"profile.region must be one of {_REGIONS}, got {region!r}")

    date_format = section.get("date_format", "").strip().lower()
    if date_format and date_format not in _DATE_FORMATS:
        raise ConfigError(
            f"profile.date_format must be one of {_DATE_FORMATS}, got {date_format!r}"
        )

    page_size = section.get("page_size", "").strip().lower()
    if page_size and page_size not in _PAGE_SIZES:
        raise ConfigError(f"profile.page_size must be one of {_PAGE_SIZES}, got {page_size!r}")

    urls_raw = section.get("apprise_urls", "").strip()
    apprise_urls = [url.strip() for url in urls_raw.split(",") if url.strip()]

    stale_after_minutes = None
    stale_str = section.get("stale_after_minutes", "").strip()
    if stale_str:
        try:
            stale_after_minutes = int(stale_str)
        except ValueError as exc:
            raise ConfigError("profile.stale_after_minutes must be an integer") from exc
        if stale_after_minutes < 0:
            raise ConfigError("profile.stale_after_minutes must be zero or positive")

    low_spo2_percent = None
    low_spo2_str = section.get("low_spo2_percent", "").strip()
    if low_spo2_str:
        try:
            low_spo2_percent = int(low_spo2_str)
        except ValueError as exc:
            raise ConfigError("profile.low_spo2_percent must be an integer") from exc
        if low_spo2_percent < 0:
            raise ConfigError("profile.low_spo2_percent must be zero or positive")

    return ProfileConfig(
        name=section.get("name", "").strip(),
        email=section.get("email", "").strip(),
        notes=section.get("notes", "").strip(),
        region=region,
        date_format=date_format,
        page_size=page_size,
        apprise_urls=apprise_urls,
        stale_after_minutes=stale_after_minutes,
        low_spo2_percent=low_spo2_percent,
    )


def load_mqtt_config(config_path: str) -> MqttConfig:
    """Load the ``[mqtt]`` section of the daemon config file, if present.

    Args:
        config_path: Path to the INI configuration file.

    Returns:
        The parsed MQTT configuration, or ``DEFAULT_MQTT_CONFIG`` (disabled)
        if the file has no ``[mqtt]`` section.

    Raises:
        ConfigError: If the file is missing or a ``[mqtt]`` value is invalid.
    """
    path = Path(config_path)
    if not path.is_file():
        raise ConfigError(f"Config file not found: {path}")

    parser = configparser.ConfigParser(interpolation=None)
    parser.read(path)

    if not parser.has_section("mqtt"):
        return DEFAULT_MQTT_CONFIG

    mqtt = parser["mqtt"]
    enabled = _parse_bool(mqtt.get("enabled", "no"), "mqtt.enabled")

    host = mqtt.get("host", "").strip()
    if enabled and not host:
        raise ConfigError("mqtt.host must be set when mqtt.enabled = yes")

    try:
        port = int(mqtt.get("port", str(DEFAULT_MQTT_CONFIG.port)))
    except ValueError as exc:
        raise ConfigError("mqtt.port must be an integer") from exc

    try:
        qos = int(mqtt.get("qos", str(DEFAULT_MQTT_CONFIG.qos)))
    except ValueError as exc:
        raise ConfigError("mqtt.qos must be an integer") from exc
    if qos not in _QOS_LEVELS:
        raise ConfigError(f"mqtt.qos must be one of {_QOS_LEVELS}, got {qos!r}")

    return MqttConfig(
        enabled=enabled,
        host=host,
        port=port,
        username=mqtt.get("username", "").strip(),
        password=mqtt.get("password", "").strip(),
        use_tls=_parse_bool(mqtt.get("use_tls", "no"), "mqtt.use_tls"),
        topic_prefix=mqtt.get("topic_prefix", DEFAULT_MQTT_CONFIG.topic_prefix).strip(),
        qos=qos,
        retain=_parse_bool(mqtt.get("retain", "yes"), "mqtt.retain"),
    )


def load_alert_config(config_path: str) -> AlertConfig:
    """Load the ``[alerting]`` section of the daemon config file, if present.

    Args:
        config_path: Path to the INI configuration file.

    Returns:
        The parsed alert configuration, or ``DEFAULT_ALERT_CONFIG``
        (disabled) if the file has no ``[alerting]`` section.

    Raises:
        ConfigError: If the file is missing or an ``[alerting]`` value is
            invalid, including enabling it with nothing to check or without
            any notification URLs.
    """
    path = Path(config_path)
    if not path.is_file():
        raise ConfigError(f"Config file not found: {path}")

    parser = configparser.ConfigParser(interpolation=None)
    parser.read(path)

    if not parser.has_section("alerting"):
        return DEFAULT_ALERT_CONFIG

    alerting = parser["alerting"]
    enabled = _parse_bool(alerting.get("enabled", "no"), "alerting.enabled")

    urls_raw = alerting.get("apprise_urls", "").strip()
    apprise_urls = [url.strip() for url in urls_raw.split(",") if url.strip()]
    if enabled and not apprise_urls:
        raise ConfigError("alerting.apprise_urls must be set when alerting.enabled = yes")

    try:
        stale_after_minutes = int(
            alerting.get("stale_after_minutes", str(DEFAULT_ALERT_CONFIG.stale_after_minutes))
        )
    except ValueError as exc:
        raise ConfigError("alerting.stale_after_minutes must be an integer") from exc
    if stale_after_minutes < 0:
        raise ConfigError("alerting.stale_after_minutes must be zero or positive")

    try:
        low_spo2_percent = int(
            alerting.get("low_spo2_percent", str(DEFAULT_ALERT_CONFIG.low_spo2_percent))
        )
    except ValueError as exc:
        raise ConfigError("alerting.low_spo2_percent must be an integer") from exc
    if low_spo2_percent < 0:
        raise ConfigError("alerting.low_spo2_percent must be zero or positive")

    try:
        low_battery_percent = int(
            alerting.get("low_battery_percent", str(DEFAULT_ALERT_CONFIG.low_battery_percent))
        )
    except ValueError as exc:
        raise ConfigError("alerting.low_battery_percent must be an integer") from exc
    if low_battery_percent < 0:
        raise ConfigError("alerting.low_battery_percent must be zero or positive")

    if enabled and stale_after_minutes == 0 and low_spo2_percent == 0 and low_battery_percent == 0:
        raise ConfigError(
            "alerting.enabled = yes but nothing is configured to check -- set "
            "stale_after_minutes, low_spo2_percent, or low_battery_percent"
        )

    return AlertConfig(
        enabled=enabled,
        apprise_urls=apprise_urls,
        stale_after_minutes=stale_after_minutes,
        low_spo2_percent=low_spo2_percent,
        low_battery_percent=low_battery_percent,
        state_path=alerting.get("state_path", DEFAULT_ALERT_CONFIG.state_path).strip(),
    )


def load_api_config(config_path: str) -> ApiConfig:
    """Load the ``[api]`` section of the daemon config file, if present.

    Args:
        config_path: Path to the INI configuration file.

    Returns:
        The parsed API configuration, or ``DEFAULT_API_CONFIG`` (disabled,
        bound to loopback) if the file has no ``[api]`` section.

    Raises:
        ConfigError: If the file is missing or an ``[api]`` value is invalid.
    """
    path = Path(config_path)
    if not path.is_file():
        raise ConfigError(f"Config file not found: {path}")

    parser = configparser.ConfigParser(interpolation=None)
    parser.read(path)

    if not parser.has_section("api"):
        return DEFAULT_API_CONFIG

    api = parser["api"]

    try:
        port = int(api.get("port", str(DEFAULT_API_CONFIG.port)))
    except ValueError as exc:
        raise ConfigError("api.port must be an integer") from exc

    return ApiConfig(
        enabled=_parse_bool(api.get("enabled", "no"), "api.enabled"),
        host=api.get("host", DEFAULT_API_CONFIG.host).strip() or DEFAULT_API_CONFIG.host,
        port=port,
        token=api.get("token", "").strip(),
    )


def load_file_sync_config(config_path: str) -> FileSyncConfig:
    """Load the ``[file_sync]`` section of the daemon config file, if present.

    Args:
        config_path: Path to the INI configuration file.

    Returns:
        The parsed file-sync configuration, or ``DEFAULT_FILE_SYNC_CONFIG``
        (enabled) if the file has no ``[file_sync]`` section.

    Raises:
        ConfigError: If the file is missing or a ``[file_sync]`` value is invalid.
    """
    path = Path(config_path)
    if not path.is_file():
        raise ConfigError(f"Config file not found: {path}")

    parser = configparser.ConfigParser(interpolation=None)
    parser.read(path)

    if not parser.has_section("file_sync"):
        return DEFAULT_FILE_SYNC_CONFIG

    file_sync = parser["file_sync"]
    return FileSyncConfig(
        enabled=_parse_bool(file_sync.get("enabled", "yes"), "file_sync.enabled"),
    )


def persist_discovered_address(config_path: Path, address: str) -> None:
    """Write a newly discovered device's address back to the config file.

    Rewrites only the ``address =`` line within the ``[monitor]`` section in
    place, so comments and formatting elsewhere in the file are preserved.

    Args:
        config_path: Path to the INI configuration file to update.
        address: BLE address of the discovered device.
    """
    lines = config_path.read_text().splitlines(keepends=True)
    in_monitor_section = False
    address_written = False

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_monitor_section = stripped == "[monitor]"
            continue
        if not in_monitor_section:
            continue
        if stripped.startswith("address") and "=" in stripped and not address_written:
            lines[i] = f"address = {address}\n"
            address_written = True

    config_path.write_text("".join(lines))
