"""SQLite storage backend for live readings and downloaded session files."""

from __future__ import annotations

import sqlite3
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS live_readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at TEXT NOT NULL,
    address TEXT NOT NULL,
    spo2 INTEGER,
    pulse_bpm INTEGER,
    battery INTEGER,
    battery_state INTEGER,
    perfusion_index INTEGER,
    worn INTEGER NOT NULL,
    calibrating INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    address TEXT NOT NULL,
    filename TEXT NOT NULL,
    downloaded_at TEXT NOT NULL,
    start_time TEXT NOT NULL,
    mode INTEGER,
    duration_seconds INTEGER,
    spo2_avg INTEGER,
    spo2_min INTEGER,
    spo2_below_3pct_events INTEGER,
    spo2_below_4pct_events INTEGER,
    seconds_below_90pct INTEGER,
    events_below_90pct INTEGER,
    percent_below_90pct REAL,
    o2_score REAL,
    steps INTEGER,
    record_count INTEGER,
    resolution_seconds REAL,
    UNIQUE (address, filename)
);

CREATE TABLE IF NOT EXISTS session_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    time TEXT NOT NULL,
    spo2 INTEGER,
    heart_rate INTEGER,
    acceleration INTEGER
);

CREATE INDEX IF NOT EXISTS idx_live_readings_recorded_at ON live_readings(recorded_at);
CREATE INDEX IF NOT EXISTS idx_sessions_start_time ON sessions(start_time);
CREATE INDEX IF NOT EXISTS idx_session_records_session_id ON session_records(session_id);
"""


def ensure_schema(db_path: str) -> None:
    """Create the tables if they don't already exist.

    Safe to call from any entry point (daemon, API server, etc.) regardless
    of whether the database file already exists or which one touches it
    first.

    Args:
        db_path: Filesystem path to the SQLite database file. Parent
            directories are created automatically if missing.
    """
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(_SCHEMA)
        connection.commit()
    finally:
        connection.close()


def get_synced_filenames(db_path: str, address: str) -> set[str]:
    """Return the set of session filenames already downloaded for a device.

    Args:
        db_path: Path to the SQLite database file.
        address: BLE address of the device.

    Returns:
        The set of filenames already present in ``sessions`` for this
        address, empty if none.
    """
    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute(
            "SELECT filename FROM sessions WHERE address = ?", (address,)
        ).fetchall()
        return {row[0] for row in rows}
    except sqlite3.OperationalError:
        return set()
    finally:
        connection.close()


class ReadingStore:
    """Persists live O2Ring readings to a local SQLite database.

    Args:
        db_path: Filesystem path to the SQLite database file. Parent
            directories are created automatically if missing.
    """

    def __init__(self, db_path: str) -> None:
        ensure_schema(db_path)
        self._connection = sqlite3.connect(db_path)
        self._connection.execute("PRAGMA foreign_keys = ON")

    def record_reading(
        self,
        recorded_at: str,
        address: str,
        spo2: int | None,
        pulse_bpm: int | None,
        battery: int | None,
        battery_state: int | None,
        perfusion_index: int | None,
        worn: bool,
        calibrating: bool,
    ) -> int:
        """Insert one live reading row.

        Args:
            recorded_at: ISO-8601 UTC timestamp the notification arrived.
            address: BLE address of the device that produced it.
            spo2: Blood oxygen saturation, percent, if reported.
            pulse_bpm: Pulse rate, beats per minute, if reported.
            battery: Battery level, percent, if reported.
            battery_state: Charging status (0/1/2), if reported.
            perfusion_index: Perfusion index, if reported.
            worn: Whether the device reports its sensor as on.
            calibrating: Worn, but SpO2/pulse have not stabilized yet.

        Returns:
            The inserted row's primary key.
        """
        cursor = self._connection.execute(
            """
            INSERT INTO live_readings (
                recorded_at, address, spo2, pulse_bpm, battery, battery_state,
                perfusion_index, worn, calibrating
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                recorded_at,
                address,
                spo2,
                pulse_bpm,
                battery,
                battery_state,
                perfusion_index,
                int(worn),
                int(calibrating),
            ),
        )
        self._connection.commit()
        return cursor.lastrowid

    def record_session(
        self,
        address: str,
        filename: str,
        downloaded_at: str,
        header,
        records,
    ) -> int | None:
        """Insert one downloaded session (header + records), if not already stored.

        Args:
            address: BLE address of the device the file was downloaded from.
            filename: The device-assigned file name (e.g. "20260116233312.vld").
            downloaded_at: ISO-8601 UTC timestamp of the download.
            header: A ``viatom_o2ring_ble.VldHeader``.
            records: An iterable of ``viatom_o2ring_ble.VldRecord``.

        Returns:
            The inserted session's primary key, or None if this
            (address, filename) pair was already stored -- re-downloading a
            file the daemon already has is a no-op, not an error.
        """
        cursor = self._connection.execute(
            """
            INSERT OR IGNORE INTO sessions (
                address, filename, downloaded_at, start_time, mode,
                duration_seconds, spo2_avg, spo2_min, spo2_below_3pct_events,
                spo2_below_4pct_events, seconds_below_90pct, events_below_90pct,
                percent_below_90pct, o2_score, steps, record_count, resolution_seconds
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                address,
                filename,
                downloaded_at,
                header.start_time.isoformat(),
                header.mode,
                header.duration_seconds,
                header.spo2_avg,
                header.spo2_min,
                header.spo2_below_3pct_events,
                header.spo2_below_4pct_events,
                header.seconds_below_90pct,
                header.events_below_90pct,
                header.percent_below_90pct,
                header.o2_score,
                header.steps,
                header.record_count,
                header.resolution_seconds,
            ),
        )
        if cursor.rowcount == 0:
            self._connection.rollback()
            return None

        session_id = cursor.lastrowid
        self._connection.executemany(
            """
            INSERT INTO session_records (session_id, time, spo2, heart_rate, acceleration)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (session_id, record.time.isoformat(), record.spo2, record.heart_rate,
                 record.acceleration)
                for record in records
            ],
        )
        self._connection.commit()
        return session_id

    def close(self) -> None:
        """Close the underlying database connection."""
        self._connection.close()
