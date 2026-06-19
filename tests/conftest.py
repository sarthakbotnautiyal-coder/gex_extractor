"""Pytest fixtures for gex_extractor tests.

Loads `.env` once and provides a mocked Supabase client so the test
suite never hits the network.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture(scope="session", autouse=True)
def _load_dotenv() -> None:
    """Load `.env` from project root before any test runs.

    Uses override=False so real environment variables (e.g. CI values)
    take precedence. Existing keys are preserved.
    """
    try:
        from dotenv import load_dotenv

        env_path = PROJECT_ROOT / ".env"
        if env_path.exists():
            load_dotenv(env_path, override=False)
    except ImportError:
        pass


@pytest.fixture
def reset_supabase_writer() -> None:
    """Reset the module-level SupabaseGexWriter singleton + its cached client.

    The writer's `_client` attribute caches the first created client for
    the process lifetime. Across tests we want each test to receive a
    fresh mock client, so we drop the singleton and let the next
    `get_writer()` rebuild it.
    """
    import src.supabase_writer as sw

    sw._writer_instance = None


@pytest.fixture
def mock_supabase_client() -> MagicMock:
    """Return a fully chainable MagicMock that mimics the Supabase client.

    `.schema("trading").table("gex_snapshots").insert(row).execute()` should
    return an object with a `.data` attribute (a list of dicts).
    """
    client = MagicMock(name="supabase_client")
    schema = MagicMock(name="schema")
    table = MagicMock(name="table")
    insert = MagicMock(name="insert")
    execute = MagicMock(name="execute")

    client.schema.return_value = schema
    schema.table.return_value = table
    table.insert.return_value = insert
    insert.execute.return_value = execute
    execute.data = [{"raw_id_local": 0, "source": "gex_bot"}]

    return client


@pytest.fixture
def tmp_logs_dir(tmp_path: Path) -> Path:
    """Return a fresh empty logs directory for the duration of a test."""
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    return logs_dir


@pytest.fixture
def sample_gex_message() -> str:
    """A representative Discord gex.bot message used by parser tests."""
    return (
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


@pytest.fixture
def sample_parsed(sample_gex_message: str) -> dict:
    """A parsed dict corresponding to `sample_gex_message`."""
    from src.discord_monitor import parse_gex_message

    parsed = parse_gex_message(sample_gex_message)
    assert parsed is not None, "fixture sample_gex_message must parse cleanly"
    return parsed


@pytest.fixture
def sample_db_row(sample_parsed: dict, sample_gex_message: str) -> dict:
    """A SQLite-shaped row dict for db_writer / supabase_writer tests."""
    import datetime as _dt

    return {
        "timestamp": sample_parsed["timestamp"],
        "received_at": _dt.datetime.now(_dt.timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
        "gex_by_oi": sample_parsed["gex_by_oi"],
        "gex_by_volume": sample_parsed["gex_by_volume"],
        "spot": sample_parsed["spot"],
        "major_negative_by_volume": sample_parsed["major_negative_by_volume"],
        "major_positive_by_volume": sample_parsed["major_positive_by_volume"],
        "major_negative_by_oi": sample_parsed["major_negative_by_oi"],
        "major_positive_by_oi": sample_parsed["major_positive_by_oi"],
        "zero_gamma": sample_parsed["zero_gamma"],
        "raw_message": sample_gex_message,
    }
