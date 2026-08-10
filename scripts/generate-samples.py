#!/usr/bin/env python3
"""Regenerate samples/single/*.pdf from a fixed, single-wearer fixture dataset.

Run this (with the package installed, e.g. `pip install -e .` from a
checkout) after any change to report.py's rendering, so the checked-in
samples don't go stale relative to what the code actually produces:

    ./scripts/generate-samples.py

See samples/README.md for what each file demonstrates.
"""

from __future__ import annotations

import configparser
import datetime
import io
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

from viatom_o2ring_daemon.report import main as report_main
from viatom_o2ring_daemon.storage import ReadingStore

_ADDRESS = "AA:BB:CC:DD:EE:FF"

# (recorded_at, spo2, pulse_bpm, battery). SpO2 drifts down across four
# weeks (mostly Normal, into Mild, then Moderate) and recovers in the
# fourth, to demonstrate category shading, the trend charts, the category
# pie, and the rollup layout's "Worst Category" column all having something
# to show. All readings are worn -- see report.exclude_not_worn.
_READINGS = [
    ("2026-01-05T23:00:00+00:00", 97, 62, 95),
    ("2026-01-05T23:10:00+00:00", 96, 64, 95),
    ("2026-01-05T23:20:00+00:00", 98, 61, 94),
    ("2026-01-05T23:30:00+00:00", 96, 63, 94),
    ("2026-01-05T23:40:00+00:00", 97, 65, 93),
    ("2026-01-12T23:00:00+00:00", 94, 66, 90),
    ("2026-01-12T23:10:00+00:00", 93, 68, 90),
    ("2026-01-12T23:20:00+00:00", 95, 65, 89),
    ("2026-01-12T23:30:00+00:00", 92, 70, 89),
    ("2026-01-12T23:40:00+00:00", 94, 67, 88),
    ("2026-01-19T23:00:00+00:00", 89, 74, 84),
    ("2026-01-19T23:10:00+00:00", 87, 76, 84),
    ("2026-01-19T23:20:00+00:00", 90, 72, 83),
    ("2026-01-19T23:30:00+00:00", 86, 78, 83),
    ("2026-01-19T23:40:00+00:00", 88, 75, 82),
    ("2026-01-26T23:00:00+00:00", 95, 66, 98),
    ("2026-01-26T23:10:00+00:00", 96, 64, 98),
    ("2026-01-26T23:20:00+00:00", 97, 63, 97),
    ("2026-01-26T23:30:00+00:00", 96, 65, 97),
    ("2026-01-26T23:40:00+00:00", 98, 62, 96),
]

# One downloaded overnight session, to populate the sessions summary table
# (report.include_sessions).
_SESSION_FILENAME = "20260119230000.vld"
_SESSION_START = datetime.datetime(2026, 1, 19, 23, 0, 0)
_SESSION_RECORDS = [
    (0, 97, 62, 0),
    (4, 96, 63, 0),
    (8, 94, 65, 1),
    (12, 90, 70, 1),
    (16, 88, 74, 2),
    (20, 91, 71, 1),
    (24, 95, 66, 0),
]


class _SessionHeader:
    def __init__(self, record_count: int) -> None:
        self.start_time = _SESSION_START
        self.mode = 1
        # A real overnight session runs for hours at thousands of records;
        # only a handful of representative records are stored for this
        # fixture, so duration is set directly rather than derived from
        # record_count -- the two aren't meant to be consistent here.
        self.duration_seconds = 8 * 3600
        self.spo2_avg = 93
        self.spo2_min = 88
        self.spo2_below_3pct_events = 2
        self.spo2_below_4pct_events = 1
        self.seconds_below_90pct = 480
        self.events_below_90pct = 2
        self.percent_below_90pct = 12.5
        self.o2_score = 7.8
        self.steps = 0
        self.record_count = record_count
        self.resolution_seconds = 4.0


class _SessionRecord:
    def __init__(self, offset_seconds: int, spo2: int, heart_rate: int, acceleration: int) -> None:
        self.time = _SESSION_START + datetime.timedelta(seconds=offset_seconds)
        self.spo2 = spo2
        self.heart_rate = heart_rate
        self.acceleration = acceleration


_BASE_REPORT = {
    "include_address": "yes",
    "include_summary": "yes",
    "include_categories": "yes",
    "include_sessions": "yes",
    "include_chart": "yes",
    "include_table": "yes",
    "table_layout": "full",
    "rollup_period": "week",
    "date_format": "world",
    "page_size": "letter",
    "exclude_not_worn": "yes",
}

# (output filename, [report] overrides).
_GRID_SAMPLES: list[tuple[str, dict[str, str]]] = [
    ("full-world.pdf", {"table_layout": "full", "date_format": "world"}),
    ("full-us.pdf", {"table_layout": "full", "date_format": "us"}),
    ("compact-world.pdf", {"table_layout": "compact", "date_format": "world"}),
    ("compact-us.pdf", {"table_layout": "compact", "date_format": "us"}),
    ("rollup-world.pdf", {"table_layout": "rollup", "date_format": "world"}),
    ("rollup-us.pdf", {"table_layout": "rollup", "date_format": "us"}),
    # Toggle demos.
    (
        "full-minimal.pdf",
        {"include_address": "no", "include_categories": "no", "include_summary": "no",
         "include_sessions": "no"},
    ),
    ("chart-only.pdf", {"include_table": "no", "include_sessions": "no"}),
    ("table-only.pdf", {"include_chart": "no", "include_sessions": "no"}),
]

# Profile personalization (name/email/notes header block) only makes sense
# as its own demo, since every other sample intentionally uses the blank
# default profile.
_PROFILE_SAMPLE = ("full-world-with-notes.pdf", {"table_layout": "full", "date_format": "world"})


def _build_fixture_db(db_path: Path) -> None:
    store = ReadingStore(str(db_path))
    for recorded_at, spo2, pulse_bpm, battery in _READINGS:
        store.record_reading(
            recorded_at=recorded_at,
            address=_ADDRESS,
            spo2=spo2,
            pulse_bpm=pulse_bpm,
            battery=battery,
            battery_state=0,
            perfusion_index=6,
            worn=True,
            calibrating=False,
        )
    header = _SessionHeader(len(_SESSION_RECORDS))
    records = [_SessionRecord(*args) for args in _SESSION_RECORDS]
    store.record_session(
        _ADDRESS, _SESSION_FILENAME, "2026-01-20T07:00:00+00:00", header, records
    )
    store.close()


def _write_config(
    path: Path, db_path: Path, report_overrides: dict[str, str], with_profile: bool
) -> None:
    parser = configparser.ConfigParser(interpolation=None)
    parser["monitor"] = {"address": _ADDRESS}
    parser["storage"] = {"db_path": str(db_path)}
    parser["daemon"] = {"log_level": "INFO"}
    parser["report"] = {**_BASE_REPORT, **report_overrides}
    if with_profile:
        parser["profile"] = {
            "name": "Jane Smith",
            "email": "jane@example.com",
            "notes": "Prescribed nighttime supplemental O2, target SpO2 >= 92%",
        }
    with open(path, "w") as config_file:
        parser.write(config_file)


def main() -> int:
    repo_dir = Path(__file__).resolve().parent.parent
    samples_dir = repo_dir / "samples"
    single_dir = samples_dir / "single"
    single_dir.mkdir(parents=True, exist_ok=True)

    jobs: list[tuple[str, dict[str, str], bool]] = [
        (filename, report_overrides, False) for filename, report_overrides in _GRID_SAMPLES
    ]
    filename, report_overrides = _PROFILE_SAMPLE
    jobs.append((filename, report_overrides, True))

    with tempfile.TemporaryDirectory() as workdir:
        workdir_path = Path(workdir)
        db_path = workdir_path / "readings.db"
        _build_fixture_db(db_path)

        for i, (filename, report_overrides, with_profile) in enumerate(jobs):
            config_path = workdir_path / f"{i}-{filename}.ini"
            _write_config(config_path, db_path, report_overrides, with_profile)
            output_path = single_dir / filename
            argv = ["--config", str(config_path), "--output", str(output_path)]
            print(f"==> {output_path.relative_to(samples_dir)}")
            with redirect_stdout(io.StringIO()) as captured:
                exit_code = report_main(argv)
            if exit_code != 0:
                print(captured.getvalue(), file=sys.stderr)
                print(f"Failed to generate {output_path}", file=sys.stderr)
                return 1

    print(f"Wrote {len(jobs)} sample(s) to {single_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
