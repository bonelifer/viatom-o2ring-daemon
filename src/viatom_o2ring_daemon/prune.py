"""Manually delete old live readings and downloaded sessions from the SQLite database."""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timedelta, timezone

from ._version import __version__
from .config import ConfigError, load_config


def _connect(db_path: str) -> sqlite3.Connection:
    """Open a connection with foreign keys enabled, so deleting a session cascades."""
    connection = sqlite3.connect(db_path)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def count_old_rows(db_path: str, cutoff: datetime, address: str | None) -> tuple[int, int]:
    """Count live readings and sessions recorded before ``cutoff``.

    Args:
        db_path: Path to the SQLite database file.
        cutoff: Rows older than this UTC datetime match.
        address: Restrict to a single device's BLE address, if given.

    Returns:
        (live_reading_count, session_count).
    """
    connection = _connect(db_path)
    try:
        reading_query = "SELECT COUNT(*) FROM live_readings WHERE recorded_at < ?"
        reading_params: list[str] = [cutoff.isoformat()]
        session_query = "SELECT COUNT(*) FROM sessions WHERE start_time < ?"
        session_params: list[str] = [cutoff.replace(tzinfo=None).isoformat()]
        if address:
            reading_query += " AND address = ?"
            reading_params.append(address)
            session_query += " AND address = ?"
            session_params.append(address)

        reading_count = connection.execute(reading_query, reading_params).fetchone()[0]
        session_count = connection.execute(session_query, session_params).fetchone()[0]
        return reading_count, session_count
    finally:
        connection.close()


def delete_old_rows(db_path: str, cutoff: datetime, address: str | None) -> tuple[int, int]:
    """Delete live readings and sessions recorded before ``cutoff`` and reclaim disk space.

    Deleting a session also deletes its ``session_records`` via the
    ``ON DELETE CASCADE`` foreign key.

    Args:
        db_path: Path to the SQLite database file.
        cutoff: Rows older than this UTC datetime are deleted.
        address: Restrict to a single device's BLE address, if given.

    Returns:
        (live_readings_deleted, sessions_deleted).
    """
    connection = _connect(db_path)
    try:
        reading_query = "DELETE FROM live_readings WHERE recorded_at < ?"
        reading_params: list[str] = [cutoff.isoformat()]
        session_query = "DELETE FROM sessions WHERE start_time < ?"
        session_params: list[str] = [cutoff.replace(tzinfo=None).isoformat()]
        if address:
            reading_query += " AND address = ?"
            reading_params.append(address)
            session_query += " AND address = ?"
            session_params.append(address)

        readings_deleted = connection.execute(reading_query, reading_params).rowcount
        sessions_deleted = connection.execute(session_query, session_params).rowcount
        connection.commit()
        connection.execute("VACUUM")
        return readings_deleted, sessions_deleted
    finally:
        connection.close()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="viatom-o2ring-prune",
        description=(
            "Delete live readings and sessions older than a given number of "
            "days. Dry-run by default -- pass --yes to actually delete."
        ),
    )
    parser.add_argument(
        "-V", "--version", action="version", version=f"%(prog)s {__version__}"
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "-c",
        "--config",
        help="Path to the daemon's INI config file (reads db_path from it)",
    )
    source.add_argument(
        "-d", "--db", help="Path to the SQLite database file, bypassing the config file"
    )
    parser.add_argument(
        "-o",
        "--older-than",
        dest="older_than",
        type=int,
        required=True,
        metavar="DAYS",
        help="Delete rows older than this many days",
    )
    parser.add_argument(
        "-a", "--address", help="Restrict pruning to one device's BLE address"
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Actually delete matching rows (omit for a dry run)",
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

    if args.older_than < 0:
        print("Error: --older-than must be zero or a positive number of days")
        return 1

    db_path = args.db
    if args.config:
        try:
            db_path = load_config(args.config).db_path
        except ConfigError as exc:
            print(f"Error: {exc}")
            return 1

    cutoff = datetime.now(timezone.utc) - timedelta(days=args.older_than)

    if not args.yes:
        readings, sessions = count_old_rows(db_path, cutoff, args.address)
        print(
            f"Would delete {readings} live reading(s) and {sessions} session(s) "
            f"recorded before {cutoff.strftime('%Y-%m-%d %H:%M UTC')}. "
            "Re-run with --yes to delete."
        )
        return 0

    readings, sessions = delete_old_rows(db_path, cutoff, args.address)
    print(
        f"Deleted {readings} live reading(s) and {sessions} session(s) recorded "
        f"before {cutoff.strftime('%Y-%m-%d %H:%M UTC')}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
