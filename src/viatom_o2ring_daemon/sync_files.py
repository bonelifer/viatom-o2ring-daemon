"""Download and store stored session files from the ring's onboard memory.

Separate from cli.py's live-streaming daemon on purpose: the ring holds a
single BLE connection, and streaming (a live reading every read_period)
and file transfer (block-by-block request/response) both go through the
same serialized request queue -- interleaving them mid-session isn't worth
the complexity for what's normally an occasional catch-up of overnight
recordings. This runs as its own short-lived connection, either after a
streaming session ends (see cli.py's run_daemon) or on its own schedule via
the viatom-o2ring-sync-files console script / systemd timer.

Supports both device families/protocols (see config.py's `protocol`):
legacy .vld files (viatom_o2ring_ble.O2RingClient) and the O2Ring-S's
"Format A" recordings (viatom_o2ring_ble.OxyIIClient). storage.py's
record_session() is protocol-agnostic -- it just reads attributes off
whatever header/records objects it's given -- so the OxyII path adapts
OxyIIFileHeader/OxyIIFileRecord into that same shape rather than storage.py
needing to know two different session formats.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import logging
from datetime import datetime, timedelta, timezone

from viatom_o2ring_ble import (
    InsufficientMtuError,
    O2RingClient,
    OxyIIClient,
    parse_oxyii_filename_timestamp,
)

from ._version import __version__
from .config import ConfigError, load_config
from .storage import ReadingStore, get_synced_filenames

_LOGGER = logging.getLogger("viatom_o2ring_daemon.sync_files")

CONNECT_TIMEOUT_SECONDS = 30.0


@dataclasses.dataclass
class _AdaptedSessionHeader:
    """Adapts an OxyIIFileHeader into the shape storage.record_session() expects.

    Several fields OxyIIFileHeader doesn't have (mode, percent_below_90pct,
    steps) are stored as None -- storage.py's schema already allows NULL
    for all of them, since the legacy protocol doesn't guarantee every
    field either. Format A has no embedded resolution; it's always exactly
    1 sample/second, so duration_seconds is just the record count.
    """

    start_time: datetime
    mode: int | None
    duration_seconds: int | None
    spo2_avg: int | None
    spo2_min: int | None
    spo2_below_3pct_events: int | None
    spo2_below_4pct_events: int | None
    seconds_below_90pct: int | None
    events_below_90pct: int | None
    percent_below_90pct: float | None
    o2_score: float | None
    steps: int | None
    record_count: int
    resolution_seconds: float


@dataclasses.dataclass
class _AdaptedSessionRecord:
    """Adapts an OxyIIFileRecord into the shape storage.record_session() expects."""

    time: datetime
    spo2: int
    heart_rate: int
    acceleration: int | None


def _adapt_oxyii_session(filename: str, header, records):
    """Convert an (OxyIIFileHeader, list[OxyIIFileRecord]) pair for storage.record_session().

    Format A records carry no embedded timestamp -- only the recording's
    filename does (a `YYYYMMDDhhmmss` start time) -- so absolute record
    times are derived from that plus each record's zero-based index.

    Args:
        filename: The device-assigned recording name, used to derive the
            session's start time.
        header: An OxyIIFileHeader.
        records: A list of OxyIIFileRecord.

    Returns:
        (_AdaptedSessionHeader, list[_AdaptedSessionRecord]).

    Raises:
        ValueError: If `filename` isn't in the expected timestamp format.
    """
    start_time = parse_oxyii_filename_timestamp(filename)
    adapted_header = _AdaptedSessionHeader(
        start_time=start_time,
        mode=None,
        duration_seconds=header.record_count,
        spo2_avg=header.spo2_avg,
        spo2_min=header.spo2_min,
        spo2_below_3pct_events=header.spo2_below_3pct_events,
        spo2_below_4pct_events=header.spo2_below_4pct_events,
        seconds_below_90pct=header.seconds_below_90pct,
        events_below_90pct=header.events_below_90pct,
        percent_below_90pct=None,
        o2_score=header.o2_score,
        steps=None,
        record_count=header.record_count,
        resolution_seconds=1.0,
    )
    adapted_records = [
        _AdaptedSessionRecord(
            time=start_time + timedelta(seconds=record.index),
            spo2=record.spo2,
            heart_rate=record.heart_rate,
            acceleration=None,
        )
        for record in records
    ]
    return adapted_header, adapted_records


async def _sync_legacy_files(db_path: str, address: str, adapter: str | None) -> int:
    """Sync stored .vld files from a legacy-protocol (O2Ring family) device."""
    client = O2RingClient(address, adapter=adapter, logger=_LOGGER)
    store = ReadingStore(db_path)
    downloaded = 0
    try:
        await client.async_connect(timeout=CONNECT_TIMEOUT_SECONDS)
        info = await client.get_info()
        already_synced = get_synced_filenames(db_path, address)
        pending = [name for name in info.file_names if name not in already_synced]

        for filename in pending:
            _LOGGER.info("Downloading %s from %s", filename, address)
            try:
                header, records = await client.download_and_parse_file(filename)
            except (RuntimeError, ValueError) as exc:
                _LOGGER.warning("Could not download/parse %s: %s", filename, exc)
                continue

            session_id = store.record_session(
                address,
                filename,
                datetime.now(timezone.utc).isoformat(),
                header,
                records,
            )
            if session_id is not None:
                downloaded += 1
                _LOGGER.info(
                    "Stored session %s: %d record(s), SpO2 avg %s%%, o2 score %s",
                    filename,
                    header.record_count,
                    header.spo2_avg,
                    header.o2_score,
                )
    finally:
        store.close()
        await client.async_disconnect()

    return downloaded


async def _sync_oxyii_files(db_path: str, address: str, adapter: str | None) -> int:
    """Sync stored Format A recordings from an O2Ring-S (T8520) device."""
    client = OxyIIClient(address, adapter=adapter, logger=_LOGGER)
    store = ReadingStore(db_path)
    downloaded = 0
    try:
        await client.async_connect(timeout=CONNECT_TIMEOUT_SECONDS)
        entries = await client.get_file_list()
        already_synced = get_synced_filenames(db_path, address)
        pending = [entry for entry in entries if entry.name not in already_synced]

        for entry in pending:
            _LOGGER.info("Downloading %s from %s", entry.name, address)
            try:
                header, records = await client.download_and_parse_file(entry.name)
            except (RuntimeError, ValueError, InsufficientMtuError) as exc:
                _LOGGER.warning("Could not download/parse %s: %s", entry.name, exc)
                continue

            if not header.trailer_confirmed:
                # A file can reach its full byte count before the trailer
                # has actually flushed -- skip it this cycle rather than
                # store an incomplete summary; it'll be picked up once
                # finalized, since it's still absent from `sessions`.
                _LOGGER.info(
                    "Skipping %s -- recording not finalized yet, will retry later",
                    entry.name,
                )
                continue

            try:
                adapted_header, adapted_records = _adapt_oxyii_session(
                    entry.name, header, records
                )
            except ValueError as exc:
                _LOGGER.warning(
                    "Could not parse start time from filename %s: %s", entry.name, exc
                )
                continue

            session_id = store.record_session(
                address,
                entry.name,
                datetime.now(timezone.utc).isoformat(),
                adapted_header,
                adapted_records,
            )
            if session_id is not None:
                downloaded += 1
                _LOGGER.info(
                    "Stored session %s: %d record(s), SpO2 avg %s%%, o2 score %s",
                    entry.name,
                    header.record_count,
                    header.spo2_avg,
                    header.o2_score,
                )
    finally:
        store.close()
        await client.async_disconnect()

    return downloaded


async def sync_files(
    db_path: str, address: str, adapter: str | None = None, protocol: str = "legacy"
) -> int:
    """Connect once, download any not-yet-stored session files, and disconnect.

    Args:
        db_path: Path to the SQLite database file.
        address: BLE address of the device to connect to.
        adapter: Optional Bluetooth adapter (Linux only).
        protocol: "legacy" (O2Ring/KidsO2/RingO2/O2 Max family) or "oxyii"
            (O2Ring-S / T8520) -- selects which library client to use.

    Returns:
        The number of new session files downloaded and stored.
    """
    if protocol == "oxyii":
        return await _sync_oxyii_files(db_path, address, adapter)
    return await _sync_legacy_files(db_path, address, adapter)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="viatom-o2ring-sync-files",
        description=(
            "Download stored session files from the ring's onboard memory "
            "that aren't already in the local database."
        ),
    )
    parser.add_argument(
        "-c", "--config", required=True, help="Path to the daemon's INI config file"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable debug logging"
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
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        _LOGGER.error(str(exc))
        return 1

    if not config.address:
        _LOGGER.error(
            "monitor.address is not set yet -- run viatom-o2ring-daemon at "
            "least once to discover and persist the device's address"
        )
        return 1

    downloaded = asyncio.run(
        sync_files(config.db_path, config.address, config.adapter or None, config.protocol)
    )
    print(f"Downloaded {downloaded} new session file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
