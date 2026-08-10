"""Download and store stored .vld session files from the ring's onboard memory.

Separate from cli.py's live-streaming daemon on purpose: the ring holds a
single BLE connection, and streaming (read_rt_data every read_period) and
file transfer (block-by-block request/response) both go through the same
serialized request queue -- interleaving them mid-session isn't worth the
complexity for what's normally an occasional catch-up of overnight
recordings. This runs as its own short-lived connection, either after a
streaming session ends (see cli.py's run_daemon) or on its own schedule via
the viatom-o2ring-sync-files console script / systemd timer.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime, timezone

from viatom_o2ring_ble import O2RingClient

from ._version import __version__
from .config import ConfigError, load_config
from .storage import ReadingStore, get_synced_filenames

_LOGGER = logging.getLogger("viatom_o2ring_daemon.sync_files")

CONNECT_TIMEOUT_SECONDS = 30.0


async def sync_files(db_path: str, address: str, adapter: str | None = None) -> int:
    """Connect once, download any not-yet-stored session files, and disconnect.

    Args:
        db_path: Path to the SQLite database file.
        address: BLE address of the device to connect to.
        adapter: Optional Bluetooth adapter (Linux only).

    Returns:
        The number of new session files downloaded and stored.
    """
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


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="viatom-o2ring-sync-files",
        description=(
            "Download stored .vld session files from the ring's onboard "
            "memory that aren't already in the local database."
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

    downloaded = asyncio.run(sync_files(config.db_path, config.address, config.adapter or None))
    print(f"Downloaded {downloaded} new session file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
