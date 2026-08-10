"""Check for stale, low-SpO2, or low-battery readings and notify via Apprise."""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import apprise

from ._version import __version__
from .config import (
    AlertConfig,
    ConfigError,
    ProfileConfig,
    load_alert_config,
    load_config,
    load_profile_config,
)

# Minimum time between repeat staleness alerts for the same address, so a
# frequent check doesn't re-notify every single run while data stays old.
_STALE_ALERT_THROTTLE = timedelta(hours=1)


@dataclass
class Alert:
    """One triggered alert and the Apprise URLs it should be sent to."""

    urls: list[str]
    message: str


def _load_state(state_path: str) -> dict[str, dict[str, str]]:
    """Load per-address alert state, tolerating a missing or corrupt file."""
    path = Path(state_path)
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_state(state_path: str, state: dict[str, dict[str, str]]) -> None:
    """Persist per-address alert state, creating the parent directory if needed."""
    path = Path(state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2))


def _all_addresses(db_path: str) -> list[str]:
    """Return every distinct address with at least one live reading."""
    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute("SELECT DISTINCT address FROM live_readings").fetchall()
        return [row[0] for row in rows]
    finally:
        connection.close()


def _latest_reading(db_path: str, address: str) -> tuple | None:
    """Return the most recent live reading row for one address."""
    connection = sqlite3.connect(db_path)
    try:
        return connection.execute(
            "SELECT recorded_at, spo2, battery, worn FROM live_readings "
            "WHERE address = ? ORDER BY recorded_at DESC LIMIT 1",
            (address,),
        ).fetchone()
    finally:
        connection.close()


def _effective_stale_after_minutes(alert_config: AlertConfig, profile: ProfileConfig | None) -> int:
    """Resolve the staleness threshold: the profile's override, or the global default."""
    if profile is not None and profile.stale_after_minutes is not None:
        return profile.stale_after_minutes
    return alert_config.stale_after_minutes


def _effective_low_spo2_percent(alert_config: AlertConfig, profile: ProfileConfig | None) -> int:
    """Resolve the low-SpO2 threshold: the profile's override, or the global default."""
    if profile is not None and profile.low_spo2_percent is not None:
        return profile.low_spo2_percent
    return alert_config.low_spo2_percent


def _effective_apprise_urls(alert_config: AlertConfig, profile: ProfileConfig | None) -> list[str]:
    """Resolve notification targets: the profile's override, or the global default."""
    if profile is not None and profile.apprise_urls:
        return profile.apprise_urls
    return alert_config.apprise_urls


def check_alerts(
    db_path: str,
    alert_config: AlertConfig,
    profile_config: ProfileConfig | None = None,
    now: datetime | None = None,
) -> list[Alert]:
    """Evaluate staleness, low-SpO2, and low-battery conditions.

    Checked independently for every distinct address with at least one
    reading -- normally just one, since a daemon instance already targets a
    single device, but the check still scans by address in case the same
    database is ever reused across a device swap. A low-SpO2 or
    low-battery alert only fires the first time a given "latest reading" is
    seen, so it isn't repeated on every subsequent run until a new reading
    arrives. A staleness alert repeats at most once per
    ``_STALE_ALERT_THROTTLE`` while the condition persists.

    Args:
        db_path: Path to the SQLite database file.
        alert_config: Parsed [alerting] configuration.
        profile_config: The daemon's [profile] section, for resolving
            stale_after_minutes/low_spo2_percent/apprise_urls overrides.
            None means no overrides apply.
        now: Current UTC time; injectable for testing. Defaults to
            ``datetime.now(timezone.utc)``.

    Returns:
        Triggered alerts (empty if nothing was triggered), each carrying
        its own destination URLs. The caller is responsible for actually
        sending them.
    """
    now = now or datetime.now(timezone.utc)
    state = _load_state(alert_config.state_path)
    alerts: list[Alert] = []

    urls = _effective_apprise_urls(alert_config, profile_config)
    stale_after_minutes = _effective_stale_after_minutes(alert_config, profile_config)
    low_spo2_percent = _effective_low_spo2_percent(alert_config, profile_config)

    for address in _all_addresses(db_path):
        address_state = state.get(address, {})
        row = _latest_reading(db_path, address)
        if row is None:
            continue

        recorded_at, spo2, battery, worn = row
        latest_dt = datetime.fromisoformat(recorded_at)

        if stale_after_minutes > 0:
            if now - latest_dt > timedelta(minutes=stale_after_minutes):
                last_alert = address_state.get("last_stale_alert_at")
                last_alert_dt = datetime.fromisoformat(last_alert) if last_alert else None
                if last_alert_dt is None or now - last_alert_dt > _STALE_ALERT_THROTTLE:
                    alerts.append(
                        Alert(
                            urls,
                            f"No reading from {address} in over {stale_after_minutes} "
                            f"minute(s) (last: {recorded_at})",
                        )
                    )
                    address_state["last_stale_alert_at"] = now.isoformat()
            else:
                address_state.pop("last_stale_alert_at", None)

        already_seen = address_state.get("last_seen_recorded_at") == recorded_at
        if not already_seen:
            if (
                low_spo2_percent > 0
                and worn
                and spo2 is not None
                and spo2 <= low_spo2_percent
            ):
                alerts.append(
                    Alert(urls, f"Low SpO2 reading from {address}: {spo2}% (worn)")
                )

            if (
                alert_config.low_battery_percent > 0
                and battery is not None
                and battery <= alert_config.low_battery_percent
            ):
                alerts.append(
                    Alert(urls, f"Low battery on {address}: {battery}%")
                )

        address_state["last_seen_recorded_at"] = recorded_at
        state[address] = address_state

    _save_state(alert_config.state_path, state)
    return alerts


def send_alerts(alerts: list[Alert]) -> None:
    """Send each alert via Apprise to its own destination URLs.

    Args:
        alerts: Alerts to send, each with its own resolved URL list (see
            ``check_alerts``). An alert with no URLs (global list empty and
            no profile override) is silently skipped -- there's nowhere to
            send it.
    """
    for alert in alerts:
        if not alert.urls:
            continue
        notifier = apprise.Apprise()
        for url in alert.urls:
            notifier.add(url)
        notifier.notify(title="O2Ring Alert", body=alert.message)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="viatom-o2ring-alert-check",
        description="Check for stale, low-SpO2, or low-battery readings and notify via Apprise.",
    )
    parser.add_argument(
        "-c", "--config", required=True, help="Path to the daemon's INI config file"
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

    try:
        db_path = load_config(args.config).db_path
        alert_config = load_alert_config(args.config)
        profile_config = load_profile_config(args.config)
    except ConfigError as exc:
        print(f"Error: {exc}")
        return 1

    if not alert_config.enabled:
        print("Alerting is disabled (alerting.enabled = no).")
        return 0

    alerts = check_alerts(db_path, alert_config, profile_config)
    if not alerts:
        print("No alerts triggered.")
        return 0

    send_alerts(alerts)
    for alert in alerts:
        print(f"ALERT: {alert.message}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
