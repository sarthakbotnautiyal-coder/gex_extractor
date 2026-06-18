"""Discord GEX polling monitor."""

import requests
import time
import datetime
import random
import os
import re
import sqlite3
from typing import Optional

from .db_writer import init_db, save_gex
from .log_setup import get_logger

log = get_logger("gex_extractor")

# Configuration
USER_TOKEN = os.environ.get("DISCORD_USER_TOKEN", "")
CHANNEL_ID = "1027647733219209227"
START_HOUR = 9
START_MIN = 30
END_HOUR = 16
END_MIN = 0
MIN_INTERVAL = 60
MAX_INTERVAL = 300
GEX_BOT_USERNAME = "gex.bot"

HEADERS = {
    "Authorization": USER_TOKEN,
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
}

# Parsing
FIELD_MAP = {
    "gex by oi": "gex_by_oi",
    "gex by volume": "gex_by_volume",
    "spot": "spot",
    "major negative by volume": "major_negative_by_volume",
    "major positive by volume": "major_positive_by_volume",
    "major negative by oi": "major_negative_by_oi",
    "major positive by oi": "major_positive_by_oi",
    "zero gamma": "zero_gamma",
}

TS_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\b")
ROW_RE = re.compile(r"\|\s*([^|]+?)\s*\|\s*(-?\d+\.?\d*)\s*\|")


def parse_gex_message(content: str) -> Optional[dict]:
    """Parse a gex.bot Discord message into a dict of fields."""
    parsed = {}

    ts_match = TS_RE.search(content)
    if ts_match:
        parsed["timestamp"] = ts_match.group(1)

    found_any = False
    for label, value_str in ROW_RE.findall(content):
        key = FIELD_MAP.get(label.strip().lower())
        if key is None:
            continue
        try:
            parsed[key] = float(value_str)
            found_any = True
        except ValueError:
            pass

    if not found_any or "timestamp" not in parsed:
        return None
    return parsed


def is_within_time_window() -> bool:
    """Check if current time is within market hours (EST)."""
    now = datetime.datetime.now(datetime.timezone.utc).astimezone(
        datetime.timezone(datetime.timedelta(hours=-4))
    )
    current = now.time()
    start = datetime.time(START_HOUR, START_MIN)
    end = datetime.time(END_HOUR, END_MIN)
    return start <= current <= end


class GexMonitor:
    """Discord GEX polling monitor."""

    def __init__(self, db_path: str = "data/gex.db"):
        self.db_path = db_path
        self.last_message_id = None
        self.backoff_time = 0
        self.running = False

        if not USER_TOKEN:
            raise ValueError("DISCORD_USER_TOKEN environment variable not set")

    def _dual_write_to_supabase(self, local_id: int, parsed: dict, raw: str) -> None:
        """Best-effort dual-write to Supabase."""
        try:
            from .supabase_writer import get_writer
            row = {
                "timestamp": parsed.get("timestamp"),
                "received_at": datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                "gex_by_oi": parsed.get("gex_by_oi"),
                "gex_by_volume": parsed.get("gex_by_volume"),
                "spot": parsed.get("spot"),
                "major_negative_by_volume": parsed.get("major_negative_by_volume"),
                "major_positive_by_volume": parsed.get("major_positive_by_volume"),
                "major_negative_by_oi": parsed.get("major_negative_by_oi"),
                "major_positive_by_oi": parsed.get("major_positive_by_oi"),
                "zero_gamma": parsed.get("zero_gamma"),
                "raw_message": raw,
            }
            ok = get_writer().write_snapshot(local_id=local_id, row=row)
            if not ok:
                log.debug("Supabase write queued for retry")
        except Exception as e:
            log.debug(f"Supabase dual-write skipped: {e}")

    def get_latest_messages(self, conn: sqlite3.Connection) -> bool:
        """Poll Discord for new messages."""
        if self.backoff_time > 0:
            log.info(f"Backing off for {self.backoff_time:.1f} seconds...")
            time.sleep(self.backoff_time)
            self.backoff_time = 0

        url = f"https://discord.com/api/v10/channels/{CHANNEL_ID}/messages?limit=1"

        try:
            r = requests.get(url, headers=HEADERS, timeout=15)

            if r.status_code == 200:
                messages = r.json()
                if not messages:
                    return True

                new_count = 0
                saved_count = 0
                for msg in reversed(messages):
                    msg_id = int(msg['id'])
                    if self.last_message_id is None or msg_id > self.last_message_id:
                        timestamp = msg['timestamp'][:19].replace('T', ' ')
                        author = msg['author']['username']
                        content = msg.get('content', '').strip()

                        log.info(f"[{timestamp}] {author}: {content[:80]}...")

                        if author == GEX_BOT_USERNAME:
                            parsed = parse_gex_message(content)
                            if parsed:
                                new_id = save_gex(conn, parsed, content)
                                if new_id is not None:
                                    saved_count += 1
                                    log.info(f"✓ Saved GEX id={new_id} ts={parsed['timestamp']}")
                                    self._dual_write_to_supabase(new_id, parsed, content)
                                else:
                                    log.debug(f"Duplicate timestamp {parsed['timestamp']}")

                        self.last_message_id = msg_id
                        new_count += 1

                if saved_count > 0:
                    log.info(f"→ {saved_count} GEX snapshot(s) saved")
                return True

            elif r.status_code == 429:
                retry_after = r.json().get('retry_after', 10)
                self.backoff_time = retry_after + random.uniform(8, 25)
                log.warning(f"Rate limited. Backing off for {self.backoff_time:.1f}s")
                return False

            elif r.status_code == 401:
                log.error("Invalid Discord token!")
                return False
            elif r.status_code == 403:
                log.error("No permission to Discord channel")
                return False
            else:
                log.error(f"Discord API error {r.status_code}")
                return False

        except Exception as e:
            log.error(f"Request error: {e}")
            return False

    def run(self):
        """Main polling loop."""
        init_db(self.db_path)
        conn = sqlite3.connect(self.db_path)
        self.running = True

        log.info("🚀 Discord GEX monitor started")
        log.info(f"Database: {self.db_path}")

        try:
            while self.running:
                if is_within_time_window():
                    self.get_latest_messages(conn)
                    sleep_time = random.uniform(MIN_INTERVAL, MAX_INTERVAL)
                    log.info(f"Next check in {sleep_time/60:.1f} minutes")
                    time.sleep(sleep_time)
                else:
                    now = datetime.datetime.now(datetime.timezone.utc).astimezone(
                        datetime.timezone(datetime.timedelta(hours=-4))
                    )
                    next_start = (now + datetime.timedelta(days=1)).replace(
                        hour=START_HOUR, minute=START_MIN, second=0, microsecond=0
                    )
                    sleep_seconds = (next_start - now).total_seconds()
                    hours = int(sleep_seconds // 3600)
                    minutes = int((sleep_seconds % 3600) // 60)
                    log.info(f"Outside hours. Sleeping {hours}h {minutes}m until tomorrow {START_HOUR:02d}:{START_MIN:02d} EST")
                    time.sleep(sleep_seconds)

        except KeyboardInterrupt:
            log.info("👋 Shutting down")
        finally:
            self.running = False
            conn.close()

    def stop(self):
        """Stop the monitor."""
        self.running = False
