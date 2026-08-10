"""Command-line entry point and daemon run loop."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import ssl
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import aiomqtt
from viatom_o2ring_ble import O2RingClient, Reading, RtReading, discover

from ._version import __version__
from .api import is_insecurely_exposed
from .config import (
    DEFAULT_FILE_SYNC_CONFIG,
    DEFAULT_MQTT_CONFIG,
    ConfigError,
    DaemonConfig,
    FileSyncConfig,
    MqttConfig,
    load_alert_config,
    load_api_config,
    load_config,
    load_file_sync_config,
    load_mqtt_config,
    load_profile_config,
    load_report_config,
    persist_discovered_address,
)
from .storage import ReadingStore

_LOGGER = logging.getLogger("viatom_o2ring_daemon")


async def discover_device(timeout: float = 60.0) -> str:
    """Scan for the first advertisement matching a supported O2Ring-family device.

    Args:
        timeout: Seconds to scan before giving up.

    Returns:
        The discovered device's BLE address.

    Raises:
        TimeoutError: If no supported device is found within ``timeout``.
    """
    _LOGGER.info(
        "No device configured yet - scanning for a supported ring "
        "(make sure it's powered on and nearby)..."
    )
    devices = await discover(timeout=timeout)
    if not devices:
        raise TimeoutError(f"No supported device found within {timeout}s")
    return devices[0].address


def _reading_to_row(reading: Reading | RtReading, address: str) -> dict[str, object]:
    """Flatten an RtReading (or legacy Reading) into storage-ready fields.

    RtReading (the default, CMD_RT_DATA) and the legacy Reading
    (CMD_READ_SENSORS) share every field this daemon stores except naming:
    RtReading.pulse_bpm/battery_state vs. Reading.heart_rate/charging.
    Reading also reports a `movement` value RtReading doesn't have; it isn't
    persisted, since there's no equivalent column to keep it consistent with.
    """
    is_legacy = isinstance(reading, Reading)
    return {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "address": address,
        "spo2": reading.spo2,
        "pulse_bpm": reading.heart_rate if is_legacy else reading.pulse_bpm,
        "battery": reading.battery,
        "battery_state": reading.charging if is_legacy else reading.battery_state,
        "perfusion_index": reading.perfusion_index,
        "worn": reading.worn,
        "calibrating": reading.calibrating,
    }


@asynccontextmanager
async def _mqtt_connection(mqtt_config: MqttConfig):
    """Yield a connected MQTT client, or None if disabled or unreachable.

    A broker connection failure is logged and treated as non-fatal: BLE
    reading recording to the local database is the daemon's primary job and
    must not be blocked by an MQTT outage.

    Args:
        mqtt_config: Parsed [mqtt] configuration.

    Yields:
        A connected ``aiomqtt.Client``, or None if MQTT is disabled or the
        broker could not be reached.
    """
    if not mqtt_config.enabled:
        yield None
        return

    tls_context = ssl.create_default_context() if mqtt_config.use_tls else None
    try:
        async with aiomqtt.Client(
            hostname=mqtt_config.host,
            port=mqtt_config.port,
            username=mqtt_config.username or None,
            password=mqtt_config.password or None,
            tls_context=tls_context,
        ) as client:
            _LOGGER.info(
                "Connected to MQTT broker %s:%s", mqtt_config.host, mqtt_config.port
            )
            yield client
    except aiomqtt.MqttError as exc:
        _LOGGER.warning(
            "Could not connect to MQTT broker %s:%s (%s) -- continuing without "
            "MQTT publishing",
            mqtt_config.host,
            mqtt_config.port,
            exc,
        )
        yield None


async def _publish_reading(
    client: aiomqtt.Client, mqtt_config: MqttConfig, address: str, row: dict[str, object]
) -> None:
    """Publish one reading to MQTT as a JSON payload.

    Failures are logged, not raised -- a broker hiccup shouldn't be allowed
    to propagate into the device's notification callback.

    Args:
        client: A connected MQTT client.
        mqtt_config: Supplies the topic prefix, QoS, and retain flag.
        address: The device's BLE address, used as the topic's last segment.
        row: The reading fields, as built by ``_reading_to_row``.
    """
    topic = f"{mqtt_config.topic_prefix}/{address}/state"
    try:
        await client.publish(
            topic, json.dumps(row), qos=mqtt_config.qos, retain=mqtt_config.retain
        )
    except aiomqtt.MqttError as exc:
        _LOGGER.warning("MQTT publish to %s failed: %s", topic, exc)


async def _sync_files_once(config: DaemonConfig, address: str) -> None:
    """Run a one-shot stored-session file sync after the streaming session ends.

    Imported lazily (function-local) to avoid a hard import-time dependency
    between cli.py and sync_files.py for callers that only need one of them.

    Args:
        config: Loaded daemon configuration.
        address: The device's BLE address.
    """
    from .sync_files import sync_files

    try:
        downloaded = await sync_files(config.db_path, address, config.adapter or None)
        if downloaded:
            _LOGGER.info("Synced %d new session file(s) from %s", downloaded, address)
    except Exception:
        _LOGGER.exception("Session file sync failed for %s", address)


async def run_daemon(
    config: DaemonConfig,
    once: bool = False,
    once_timeout: int = 60,
    mqtt_config: MqttConfig = DEFAULT_MQTT_CONFIG,
    file_sync_config: FileSyncConfig = DEFAULT_FILE_SYNC_CONFIG,
) -> bool:
    """Connect to the configured (or newly discovered) device and log readings.

    Args:
        config: Loaded daemon configuration.
        once: If True, exit after recording a single reading (or after
            ``once_timeout`` seconds without one) instead of running until a
            stop signal.
        once_timeout: Seconds to wait for one reading before giving up. Only
            used when ``once`` is True.
        mqtt_config: Optional MQTT publishing configuration. If enabled,
            each reading is also published as JSON. A broker outage is
            logged and non-fatal -- it never blocks local recording.
        file_sync_config: If enabled, stored .vld session files are synced
            once after the streaming session ends (on stop signal or
            --once), since the ring only has one BLE connection at a time
            and file transfer competes with live polling for it.

    Returns:
        True if at least one reading was recorded. Always True for a normal
        (non-``once``) run, which only returns via a stop signal.
    """
    address = config.address
    if not address:
        discovery_timeout = float(once_timeout) if once else 60.0
        address = await discover_device(discovery_timeout)
        persist_discovered_address(config.config_path, address)
        _LOGGER.info("Discovered device at %s - saved to %s", address, config.config_path)

    store = ReadingStore(config.db_path)
    stop_event = asyncio.Event()
    reading_received = False

    async with _mqtt_connection(mqtt_config) as mqtt_client:
        background_tasks: list[asyncio.Task] = []

        def on_reading(reading: Reading | RtReading) -> None:
            nonlocal reading_received
            row = _reading_to_row(reading, address)
            store.record_reading(**row)
            reading_received = True
            _LOGGER.info(
                "Recorded reading from %s: SpO2 %s%%, pulse %s bpm, worn=%s",
                address,
                row["spo2"],
                row["pulse_bpm"],
                row["worn"],
            )
            if mqtt_client is not None:
                background_tasks.append(
                    asyncio.create_task(
                        _publish_reading(mqtt_client, mqtt_config, address, row)
                    )
                )
            if once:
                stop_event.set()

        client = O2RingClient(
            address,
            on_reading=on_reading,
            legacy_sensors=config.legacy_sensors,
            adapter=config.adapter or None,
            logger=_LOGGER,
            cooldown_seconds=config.cooldown_seconds,
            read_period=config.read_period,
        )

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop_event.set)

        _LOGGER.info(
            "Starting viatom-o2ring-daemon %s for device at %s%s",
            __version__,
            address,
            f" (once, {once_timeout}s timeout)" if once else "",
        )
        await client.async_start()
        try:
            if once:
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=once_timeout)
                except TimeoutError:
                    _LOGGER.warning("No reading received within %s seconds", once_timeout)
            else:
                await stop_event.wait()
        finally:
            _LOGGER.info("Shutting down")
            await client.async_stop()
            if background_tasks:
                await asyncio.wait(background_tasks, timeout=5)
            store.close()

    if file_sync_config.enabled:
        await _sync_files_once(config, address)

    return reading_received


def _check_config(config_path: str) -> int:
    """Validate a config file against every section loader, without running.

    Args:
        config_path: Path to the INI configuration file.

    Returns:
        0 if the file is valid (a summary is printed), 1 otherwise (each
        error is printed).
    """
    if not Path(config_path).is_file():
        print(f"Error: Config file not found: {config_path}")
        return 1

    errors: list[str] = []
    daemon_config = report_config = None
    mqtt_config = alert_config = api_config = profile_config = file_sync_config = None

    try:
        daemon_config = load_config(config_path)
    except ConfigError as exc:
        errors.append(str(exc))
    try:
        report_config = load_report_config(config_path)
    except ConfigError as exc:
        errors.append(str(exc))
    try:
        mqtt_config = load_mqtt_config(config_path)
    except ConfigError as exc:
        errors.append(str(exc))
    try:
        alert_config = load_alert_config(config_path)
    except ConfigError as exc:
        errors.append(str(exc))
    try:
        api_config = load_api_config(config_path)
    except ConfigError as exc:
        errors.append(str(exc))
    try:
        profile_config = load_profile_config(config_path)
    except ConfigError as exc:
        errors.append(str(exc))
    try:
        file_sync_config = load_file_sync_config(config_path)
    except ConfigError as exc:
        errors.append(str(exc))

    if errors:
        print(f"{config_path}: INVALID")
        for error in errors:
            print(f"  - {error}")
        return 1

    print(f"{config_path}: OK")
    print(
        "  monitor: address="
        f"{daemon_config.address or '(auto-discover)'} adapter="
        f"{daemon_config.adapter or '(default)'} "
        f"legacy_sensors={'yes' if daemon_config.legacy_sensors else 'no'}"
    )
    print(f"  storage: db_path={daemon_config.db_path}")
    print(f"  daemon: log_level={daemon_config.log_level}")
    print(
        "  report: date_format="
        f"{report_config.date_format} page_size={report_config.page_size} "
        f"table_layout={report_config.table_layout}"
    )
    print(
        "  mqtt: enabled="
        f"{'yes' if mqtt_config.enabled else 'no'} "
        f"host={mqtt_config.host or '(unset)'} port={mqtt_config.port}"
    )
    print(
        "  alerting: enabled="
        f"{'yes' if alert_config.enabled else 'no'} "
        f"stale_after_minutes={alert_config.stale_after_minutes} "
        f"low_spo2_percent={alert_config.low_spo2_percent} "
        f"low_battery_percent={alert_config.low_battery_percent} "
        f"urls={len(alert_config.apprise_urls)}"
    )
    print(
        "  api: enabled="
        f"{'yes' if api_config.enabled else 'no'} "
        f"host={api_config.host} port={api_config.port} "
        f"token={'(set)' if api_config.token else '(none)'}"
    )
    print(
        f"  profile: name={profile_config.name or '(unset)'} "
        f"region={profile_config.region or '(unset)'}"
    )
    print(f"  file_sync: enabled={'yes' if file_sync_config.enabled else 'no'}")
    if is_insecurely_exposed(api_config):
        print(
            f"  warning: api.host is {api_config.host!r} (not loopback) but "
            "api.token is unset -- anyone who can reach this address can read "
            "readings and generate reports. Set api.token, or bind to 127.0.0.1."
        )
    return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="viatom-o2ring-daemon",
        description=(
            "Standalone BLE daemon that logs Viatom/Wellue O2Ring pulse "
            "oximeter readings to a local SQLite database."
        ),
    )
    parser.add_argument(
        "-c", "--config", required=True, help="Path to the daemon's INI configuration file"
    )
    parser.add_argument(
        "-k",
        "--check-config",
        action="store_true",
        help="Validate the config file and exit, without starting the daemon",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging (overrides the config file's log level)",
    )
    parser.add_argument(
        "-o",
        "--once",
        action="store_true",
        help=(
            "Record one reading and exit, instead of running until stopped "
            "(run by hand for a quick spot-check instead of a long-running "
            "service)"
        ),
    )
    parser.add_argument(
        "-w",
        "--once-timeout",
        dest="once_timeout",
        type=int,
        default=60,
        metavar="SECONDS",
        help="Seconds to wait for a reading in --once mode (default: %(default)s)",
    )
    parser.add_argument(
        "-V", "--version", action="version", version=f"%(prog)s {__version__}"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Optional argument list (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code.
    """
    args = _parse_args(argv)

    if args.check_config:
        return _check_config(args.config)

    try:
        config = load_config(args.config)
        mqtt_config = load_mqtt_config(args.config)
        file_sync_config = load_file_sync_config(args.config)
    except ConfigError as exc:
        logging.basicConfig(level=logging.ERROR)
        _LOGGER.error(str(exc))
        return 1

    log_level = "DEBUG" if args.verbose else config.log_level
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        reading_received = asyncio.run(
            run_daemon(
                config,
                once=args.once,
                once_timeout=args.once_timeout,
                mqtt_config=mqtt_config,
                file_sync_config=file_sync_config,
            )
        )
    except (TimeoutError, ConfigError) as exc:
        _LOGGER.error(str(exc))
        return 1
    except KeyboardInterrupt:
        return 0

    if args.once and not reading_received:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
