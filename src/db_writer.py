"""Database writer for GEX snapshots."""

import sqlite3
import datetime
import os


def init_db(db_path: str) -> None:
    """Initialize SQLite database for GEX snapshots."""
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS gex_snapshots (
            id                       INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp                TEXT    NOT NULL UNIQUE,
            received_at              TEXT    NOT NULL,
            gex_by_oi                REAL,
            gex_by_volume            REAL,
            spot                     REAL,
            major_negative_by_volume REAL,
            major_positive_by_volume REAL,
            major_negative_by_oi     REAL,
            major_positive_by_oi     REAL,
            zero_gamma               REAL,
            raw_message              TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_gex_ts ON gex_snapshots(timestamp)")
    conn.commit()
    conn.close()


def save_gex(conn: sqlite3.Connection, parsed: dict, raw: str) -> int | None:
    """Insert a parsed GEX snapshot. Returns the new row id, or None on duplicate."""
    try:
        cur = conn.execute("""
            INSERT INTO gex_snapshots (
                timestamp, received_at,
                gex_by_oi, gex_by_volume, spot,
                major_negative_by_volume, major_positive_by_volume,
                major_negative_by_oi, major_positive_by_oi,
                zero_gamma, raw_message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            parsed.get("timestamp"),
            datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            parsed.get("gex_by_oi"),
            parsed.get("gex_by_volume"),
            parsed.get("spot"),
            parsed.get("major_negative_by_volume"),
            parsed.get("major_positive_by_volume"),
            parsed.get("major_negative_by_oi"),
            parsed.get("major_positive_by_oi"),
            parsed.get("zero_gamma"),
            raw,
        ))
        conn.commit()
        return int(cur.lastrowid)
    except sqlite3.IntegrityError:
        return None
