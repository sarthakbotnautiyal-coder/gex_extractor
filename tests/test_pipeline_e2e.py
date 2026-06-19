"""End-to-end pipeline test (parse → SQLite → mocked Supabase).

This is the kept happy-path from the old validation_test.py. It exercises
the real parser and real db_writer against a tmp_path SQLite file, then
a mocked Supabase client for the cloud step. No network is hit.
"""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest

from src.db_writer import init_db, save_gex
from src.discord_monitor import parse_gex_message


SAMPLE_GEX_MESSAGE = (
    "2026-06-19 15:59:59     |     <https://gexbot.com>\n"
    "```\n"
    "| SPX Gamma                |          |\n"
    "|--------------------------|----------|\n"
    "| GEX by OI                |   -40.29 |\n"
    "| GEX by Volume            | -1246.41 |\n"
    "|                          |          |\n"
    "| Spot                     |  7497.86 |\n"
    "|                          |          |\n"
    "| Major Negative by Volume |  7500.00 |\n"
    "| Major Positive by Volume |  7505.83 |\n"
    "| Major Negative by OI     |  7500.00 |\n"
    "| Major Positive by OI     |  7700.00 |\n"
    "| Zero Gamma               |  7504.74 |\n"
    "```"
)


def test_parse_then_sqlite_then_mocked_supabase(
    mock_supabase_client, tmp_path, reset_supabase_writer
) -> None:
    # 1. Parse the Discord message.
    parsed = parse_gex_message(SAMPLE_GEX_MESSAGE)
    assert parsed is not None

    # 2. Persist locally.
    db_path = str(tmp_path / "e2e.db")
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        local_id = save_gex(conn, parsed, SAMPLE_GEX_MESSAGE)
        assert isinstance(local_id, int)
    finally:
        conn.close()

    # 3. Mocked Supabase write.
    row = {
        "timestamp": parsed["timestamp"],
        "received_at": "2026-06-19 16:00:00",
        "gex_by_oi": parsed["gex_by_oi"],
        "gex_by_volume": parsed["gex_by_volume"],
        "spot": parsed["spot"],
        "major_negative_by_volume": parsed["major_negative_by_volume"],
        "major_positive_by_volume": parsed["major_positive_by_volume"],
        "major_negative_by_oi": parsed["major_negative_by_oi"],
        "major_positive_by_oi": parsed["major_positive_by_oi"],
        "zero_gamma": parsed["zero_gamma"],
        "raw_message": SAMPLE_GEX_MESSAGE,
    }
    with patch("src.supabase_writer.create_client", return_value=mock_supabase_client):
        from src.supabase_writer import get_writer

        writer = get_writer()
        ok = writer.write_snapshot(local_id=local_id, row=row)
    assert ok is True
