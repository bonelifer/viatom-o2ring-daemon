"""Lightweight local HTTP API: latest reading, recorded sessions, and on-demand reports.

Reads from the same SQLite database as everything else in this package --
it's a standalone view onto that data, not part of the daemon's BLE
connection lifecycle, so it works whether or not the daemon is currently
running.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import tempfile

from aiohttp import web

from ._version import __version__
from .config import (
    ApiConfig,
    ConfigError,
    load_api_config,
    load_config,
    load_profile_config,
    load_report_config,
)
from .report import (
    _apply_profile_overrides,
    _resolve_range,
    build_csv,
    build_pdf,
    build_session_records_csv,
    fetch_rows,
    fetch_session_records,
    fetch_sessions,
)
from .storage import ensure_schema

_VALID_FORMATS = ("pdf", "csv")
_VALID_RECORD_FORMATS = ("json", "csv")
_VALID_PERIODS = ("7d", "30d", "90d", "1y", "all")

_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


def is_insecurely_exposed(api_config: ApiConfig) -> bool:
    """Return whether the API is bound to a non-loopback address with no auth token."""
    return api_config.host not in _LOOPBACK_HOSTS and not api_config.token


def _latest_reading(db_path: str, address: str | None) -> dict[str, object] | None:
    """Return the most recent live reading, optionally restricted to one address.

    Args:
        db_path: Path to the SQLite database file.
        address: Restrict to a single device's BLE address, if given.

    Returns:
        A dict of the reading's fields, or None if no readings match.
    """
    query = (
        "SELECT id, recorded_at, address, spo2, pulse_bpm, battery, battery_state, "
        "perfusion_index, worn, calibrating FROM live_readings"
    )
    params: list[str] = []
    if address:
        query += " WHERE address = ?"
        params.append(address)
    query += " ORDER BY recorded_at DESC LIMIT 1"

    connection = sqlite3.connect(db_path)
    try:
        row = connection.execute(query, params).fetchone()
    finally:
        connection.close()

    if row is None:
        return None
    return {
        "id": row[0],
        "recorded_at": row[1],
        "address": row[2],
        "spo2": row[3],
        "pulse_bpm": row[4],
        "battery": row[5],
        "battery_state": row[6],
        "perfusion_index": row[7],
        "worn": bool(row[8]),
        "calibrating": bool(row[9]),
    }


def _recent_sessions(db_path: str, address: str | None, limit: int) -> list[dict[str, object]]:
    """Return the most recently downloaded sessions, newest first.

    Args:
        db_path: Path to the SQLite database file.
        address: Restrict to a single device's BLE address, if given.
        limit: Maximum number of sessions to return.

    Returns:
        One dict per session, newest first.
    """
    query = (
        "SELECT filename, address, start_time, duration_seconds, spo2_avg, spo2_min, "
        "spo2_below_3pct_events, spo2_below_4pct_events, o2_score, steps FROM sessions"
    )
    params: list[object] = []
    if address:
        query += " WHERE address = ?"
        params.append(address)
    query += " ORDER BY start_time DESC LIMIT ?"
    params.append(limit)

    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute(query, params).fetchall()
    finally:
        connection.close()

    return [
        {
            "filename": row[0],
            "address": row[1],
            "start_time": row[2],
            "duration_seconds": row[3],
            "spo2_avg": row[4],
            "spo2_min": row[5],
            "spo2_below_3pct_events": row[6],
            "spo2_below_4pct_events": row[7],
            "o2_score": row[8],
            "steps": row[9],
        }
        for row in rows
    ]


def _require_auth(request: web.Request) -> web.Response | None:
    """Return a 401 response if a token is configured and missing/wrong."""
    token = request.app["api_token"]
    if not token:
        return None
    if request.headers.get("Authorization", "") != f"Bearer {token}":
        return web.json_response({"error": "unauthorized"}, status=401)
    return None


async def handle_health(request: web.Request) -> web.Response:
    """GET /health -- unauthenticated liveness check."""
    return web.json_response({"status": "ok", "version": __version__})


async def handle_latest(request: web.Request) -> web.Response:
    """GET /latest[?address=...] -- most recent live reading, as JSON."""
    unauthorized = _require_auth(request)
    if unauthorized is not None:
        return unauthorized

    reading = _latest_reading(request.app["db_path"], request.query.get("address"))
    if reading is None:
        return web.json_response({"error": "no readings found"}, status=404)
    return web.json_response(reading)


async def handle_sessions(request: web.Request) -> web.Response:
    """GET /sessions[?address=...&limit=...] -- most recently downloaded sessions, as JSON."""
    unauthorized = _require_auth(request)
    if unauthorized is not None:
        return unauthorized

    try:
        limit = int(request.query.get("limit", "20"))
    except ValueError:
        return web.json_response({"error": "limit must be an integer"}, status=400)
    if limit <= 0:
        return web.json_response({"error": "limit must be positive"}, status=400)

    sessions = _recent_sessions(request.app["db_path"], request.query.get("address"), limit)
    return web.json_response(sessions)


async def handle_session_records(request: web.Request) -> web.Response:
    """GET /session-records?filename=...[&address=...&format=json|csv].

    Returns one downloaded session's raw per-sample records -- the
    every-2-or-4-seconds data the /sessions summary was computed from.
    Defaults to JSON; ``format=csv`` returns a file download instead,
    matching what ``viatom-o2ring-report --export-session`` writes.
    """
    unauthorized = _require_auth(request)
    if unauthorized is not None:
        return unauthorized

    filename = request.query.get("filename")
    if not filename:
        return web.json_response({"error": "filename is required"}, status=400)

    fmt = request.query.get("format", "json")
    if fmt not in _VALID_RECORD_FORMATS:
        return web.json_response(
            {"error": f"format must be one of {_VALID_RECORD_FORMATS}"}, status=400
        )

    records = fetch_session_records(
        request.app["db_path"], filename, request.query.get("address")
    )
    if not records:
        return web.json_response(
            {"error": f"no session found matching filename {filename!r}"}, status=404
        )

    if fmt == "json":
        return web.json_response(
            [
                {
                    "time": record.time.isoformat(),
                    "spo2": record.spo2,
                    "heart_rate": record.heart_rate,
                    "acceleration": record.acceleration,
                }
                for record in records
            ]
        )

    fd, temp_path = tempfile.mkstemp(suffix=".csv")
    os.close(fd)
    try:
        build_session_records_csv(records, temp_path)
        with open(temp_path, "rb") as csv_file:
            body = csv_file.read()
    finally:
        os.remove(temp_path)

    return web.Response(
        body=body,
        content_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}-records.csv"'},
    )


async def handle_report(request: web.Request) -> web.Response:
    """GET /report[?format=pdf|csv&period=...&from=...&to=...&address=...].

    Generates a report on demand using the same config-driven settings as
    ``viatom-o2ring-report`` and returns it as a file download.
    """
    unauthorized = _require_auth(request)
    if unauthorized is not None:
        return unauthorized

    fmt = request.query.get("format", "pdf")
    if fmt not in _VALID_FORMATS:
        return web.json_response({"error": f"format must be one of {_VALID_FORMATS}"}, status=400)

    period = request.query.get("period", "all")
    if period not in _VALID_PERIODS:
        return web.json_response({"error": f"period must be one of {_VALID_PERIODS}"}, status=400)

    try:
        start, end = _resolve_range(period, request.query.get("from"), request.query.get("to"))
    except ValueError as exc:
        return web.json_response({"error": f"invalid date: {exc}"}, status=400)

    address = request.query.get("address")
    report_config = request.app["report_config"]
    profile_config = request.app["profile_config"]
    effective_report_config = _apply_profile_overrides(report_config, profile_config)

    rows = fetch_rows(
        request.app["db_path"], address, start, end, effective_report_config.exclude_not_worn
    )
    sessions = fetch_sessions(request.app["db_path"], address, start, end)
    if not rows and not sessions:
        return web.json_response(
            {"error": "no readings or sessions found for the given range/filters"}, status=404
        )

    fd, temp_path = tempfile.mkstemp(suffix=f".{fmt}")
    os.close(fd)
    try:
        if fmt == "csv":
            build_csv(rows, temp_path, effective_report_config)
            content_type = "text/csv"
        else:
            build_pdf(rows, temp_path, effective_report_config, profile_config, sessions)
            content_type = "application/pdf"
        with open(temp_path, "rb") as report_file:
            body = report_file.read()
    finally:
        os.remove(temp_path)

    return web.Response(
        body=body,
        content_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="o2ring-report.{fmt}"'},
    )


def build_app(
    db_path: str, api_config: ApiConfig, report_config, profile_config
) -> web.Application:
    """Build the aiohttp application with routes and shared state attached.

    Args:
        db_path: Path to the SQLite database file.
        api_config: Supplies the auth token.
        report_config: Used for on-demand report generation.
        profile_config: Supplies the wearer's report personalization.

    Returns:
        A configured, unstarted aiohttp Application.
    """
    app = web.Application()
    app["db_path"] = db_path
    app["api_token"] = api_config.token
    app["report_config"] = report_config
    app["profile_config"] = profile_config
    app.router.add_get("/health", handle_health)
    app.router.add_get("/latest", handle_latest)
    app.router.add_get("/sessions", handle_sessions)
    app.router.add_get("/session-records", handle_session_records)
    app.router.add_get("/report", handle_report)
    return app


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="viatom-o2ring-api",
        description="Lightweight local HTTP API: latest reading, sessions, and on-demand reports.",
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
        Process exit code. Only returns while disabled or on a config
        error -- otherwise blocks forever serving requests.
    """
    args = _parse_args(argv)

    try:
        db_path = load_config(args.config).db_path
        api_config = load_api_config(args.config)
        report_config = load_report_config(args.config)
        profile_config = load_profile_config(args.config)
    except ConfigError as exc:
        print(f"Error: {exc}")
        return 1

    if not api_config.enabled:
        print("API is disabled (api.enabled = no).")
        return 0

    if is_insecurely_exposed(api_config):
        print(
            f"WARNING: api.host is {api_config.host!r} (not loopback) but api.token "
            "is unset -- anyone who can reach this address can read readings and "
            "generate reports. Set api.token, or bind to 127.0.0.1 and put a "
            "reverse proxy with its own auth in front if you need remote access."
        )

    ensure_schema(db_path)
    app = build_app(db_path, api_config, report_config, profile_config)
    print(f"Listening on http://{api_config.host}:{api_config.port}")
    web.run_app(app, host=api_config.host, port=api_config.port, print=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
