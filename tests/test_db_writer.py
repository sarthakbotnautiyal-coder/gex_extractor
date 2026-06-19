"""Tests for src.db_writer (init_db + save_gex)."""

from __future__ import annotations

import sqlite3

import pytest

from src.db_writer import init_db, save_gex


@pytest.fixture
def db_path(tmp_path) -> str:
    """Return a fresh SQLite path inside tmp_path. The file does not exist yet."""
    p = tmp_path / "gex.db"
    return str(p)


@pytest.fixture
def conn(db_path: str):
    """A SQLite connection to a freshly-initialized test DB."""
    init_db(db_path)
    c = sqlite3.connect(db_path)
    try:
        yield c
    finally:
        c.close()


def test_init_db_creates_table(db_path: str) -> None:
    init_db(db_path)
    con = sqlite3.connect(db_path)
    try:
        cur = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='gex_snapshots'"
        )
        assert cur.fetchone() is not None, "gex_snapshots table should exist"
    finally:
        con.close()


def test_init_db_creates_index(db_path: str) -> None:
    init_db(db_path)
    con = sqlite3.connect(db_path)
    try:
        cur = con.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name='idx_gex_ts'"
        )
        assert cur.fetchone() is not None, "idx_gex_ts index should exist"
    finally:
        con.close()


def test_save_gex_inserts_row_and_returns_id(conn: sqlite3.Connection) -> None:
    parsed = {
        "timestamp": "2026-06-19 15:59:59",
        "gex_by_oi": -40.29,
        "gex_by_volume": -1246.41,
        "spot": 7497.86,
        "major_negative_by_volume": 7500.00,
        "major_positive_by_volume": 7505.83,
        "major_negative_by_oi": 7500.00,
        "major_positive_by_oi": 7700.00,
        "zero_gamma": 7504.74,
    }
    new_id = save_gex(conn, parsed, "raw message text")
    assert isinstance(new_id, int)
    assert new_id > 0

    row = conn.execute(
        "SELECT id, timestamp, spot, zero_gamma, raw_message "
        "FROM gex_snapshots WHERE id = ?",
        (new_id,),
    ).fetchone()
    assert row is not None
    assert row[1] == "2026-06-19 15:59:59"
    assert row[2] == pytest.approx(7497.86)
    assert row[3] == pytest.approx(7504.74)
    assert row[4] == "raw message text"


def test_save_gex_returns_none_on_duplicate_timestamp(conn: sqlite3.Connection) -> None:
    parsed = {
        "timestamp": "2026-06-19 15:59:59",
        "spot": 7497.86,
        "zero_gamma": 7504.74,
    }
    first_id = save_gex(conn, parsed, "raw")
    assert isinstance(first_id, int)

    # Same timestamp, different other fields — must be rejected by UNIQUE.
    second_id = save_gex(conn, parsed, "raw again")
    assert second_id is None, "Duplicate timestamp should return None"


def test_save_gex_handles_missing_optional_fields(conn: sqlite3.Connection) -> None:
    parsed = {"timestamp": "2026-06-19 16:00:00"}
    new_id = save_gex(conn, parsed, "raw")
    assert isinstance(new_id, int)
    row = conn.execute(
        "SELECT spot, zero_gamma FROM gex_snapshots WHERE id = ?", (new_id,)
    ).fetchone()
    assert row is not None
    assert row[0] is None
    assert row[1] is None
