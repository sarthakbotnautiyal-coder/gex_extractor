#!/usr/bin/env python3
"""GEX Extractor - Discord GEX polling service."""

import os
import signal
import sys
from pathlib import Path

# Run as `python -m src.discord_monitor` from project root — handled by main()
PROJECT_ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

# Load .env so os.environ gets the tokens
try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass

from src.log_setup import get_logger
from src.discord_monitor import GexMonitor

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
