#!/usr/bin/env python3
"""GEX Extractor - Discord GEX polling service."""

import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from discord_monitor import GexMonitor
from log_setup import get_logger

log = get_logger("gex_extractor")


def main():
    monitor = GexMonitor()

    def handle_sigterm(signum, frame):
        log.info("SIGTERM received, shutting down gracefully...")
        monitor.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_sigterm)

    try:
        monitor.run()
    except KeyboardInterrupt:
        log.info("Interrupted, shutting down...")
        sys.exit(0)


if __name__ == "__main__":
    main()
