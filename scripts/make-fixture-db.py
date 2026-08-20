#!/usr/bin/env python3
"""Create a tiny fixture SQLite database for smoke/CI testing.

The schema here is duplicated from storage.py's _SCHEMA rather than
imported, so this script has no dependency on the package being installed.
Keep the two in sync if the tables' columns change.
"""

import sqlite3
import sys
from datetime import datetime, timezone


def main() -> None:
    db_path = sys.argv[1]
    con = sqlite3.connect(db_path)
    con.execute(
        """
        CREATE TABLE live_readings (
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
        )
        """
    )
    con.execute(
        "INSERT INTO live_readings "
        "(recorded_at, address, spo2, pulse_bpm, battery, battery_state, "
        "perfusion_index, worn, calibrating) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            datetime.now(timezone.utc).isoformat(),
            "AA:BB:CC:DD:EE:FF",
            97,
            68,
            80,
            0,
            8,
            1,
            0,
        ),
    )
    con.execute(
        """
        CREATE TABLE sessions (
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
        )
        """
    )
    con.execute(
        """
        CREATE TABLE session_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            time TEXT NOT NULL,
            spo2 INTEGER,
            heart_rate INTEGER,
            acceleration INTEGER
        )
        """
    )
    con.commit()
    con.close()


if __name__ == "__main__":
    main()
