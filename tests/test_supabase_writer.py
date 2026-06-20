"""Tests for src.supabase_writer (no network).

The mocked Supabase client fixture (`mock_supabase_client` from
conftest.py) replaces `src.supabase_writer.create_client` so no real
HTTP/Supabase call is made.
"""

from __future__ import annotations

import builtins
import json
import logging
from unittest.mock import patch

import pytest

import src.supabase_writer as sw


# ---------------------------------------------------------------------------
# normalize_timestamp / to_cloud_row / write_snapshot happy + edge cases
# ---------------------------------------------------------------------------


def test_normalize_timestamp_passes_through_none() -> None:
    assert sw._normalize_timestamp(None) is None


def test_normalize_timestamp_passes_through_empty() -> None:
    assert sw._normalize_timestamp("") is None
    assert sw._normalize_timestamp("   ") is None


def test_normalize_timestamp_passes_through_iso() -> None:
    assert sw._normalize_timestamp("2026-06-19T15:59:59+00:00") == \
        "2026-06-19T15:59:59+00:00"
    assert sw._normalize_timestamp("2026-06-19T15:59:59Z") == \
        "2026-06-19T15:59:59Z"


def test_normalize_timestamp_converts_naive_to_iso() -> None:
    # Naive values are ET wall-clock → America/New_York (DST-aware).
    assert sw._normalize_timestamp("2026-06-19 15:59:59") == \
        "2026-06-19T15:59:59-04:00"  # June → EDT
    assert sw._normalize_timestamp("2026-01-19 15:59:59") == \
        "2026-01-19T15:59:59-05:00"  # January → EST


def test_normalize_timestamp_passes_through_garbage() -> None:
    # Unparseable input falls through unchanged.
    assert sw._normalize_timestamp("not a timestamp") == "not a timestamp"


def test_to_cloud_row_maps_all_fields(sample_db_row: dict) -> None:
    cloud_row = sw._to_cloud_row(42, sample_db_row)
    assert cloud_row["raw_id_local"] == 42
    assert cloud_row["source"] == sw.GEX_SOURCE
    assert cloud_row["snapshot_timestamp"] == "2026-06-19T15:59:59-04:00"
    assert cloud_row["received_at"] is not None
    assert cloud_row["gex_by_oi"] == sample_db_row["gex_by_oi"]
    assert cloud_row["gex_by_volume"] == sample_db_row["gex_by_volume"]
    assert cloud_row["spot"] == sample_db_row["spot"]
    assert cloud_row["zero_gamma"] == sample_db_row["zero_gamma"]
    assert cloud_row["raw_message"] == sample_db_row["raw_message"]


def test_write_snapshot_returns_true_on_mocked_success(
    mock_supabase_client,
    sample_db_row: dict,
    reset_supabase_writer,
) -> None:
    with patch("src.supabase_writer.create_client", return_value=mock_supabase_client):
        from src.supabase_writer import get_writer

        writer = get_writer()
        ok = writer.write_snapshot(local_id=42, row=sample_db_row)

    assert ok is True


def test_write_snapshot_calls_insert_with_cloud_row(
    mock_supabase_client,
    sample_db_row: dict,
    reset_supabase_writer,
) -> None:
    with patch("src.supabase_writer.create_client", return_value=mock_supabase_client):
        from src.supabase_writer import get_writer

        writer = get_writer()
        writer.write_snapshot(local_id=42, row=sample_db_row)

    # The chain must reach .insert(cloud_row).execute()
    mock_supabase_client.schema.assert_called_with("trading")
    mock_supabase_client.schema.return_value.table.assert_called_with("gex_snapshots")
    insert_call = mock_supabase_client.schema.return_value.table.return_value.insert
    assert insert_call.called
    cloud_row = insert_call.call_args[0][0]
    assert cloud_row["raw_id_local"] == 42
    assert cloud_row["source"] == "gex_bot"
    # _normalize_timestamp converts naive ET "2026-06-19 15:59:59" -> ISO 8601 EDT.
    assert cloud_row["snapshot_timestamp"] == "2026-06-19T15:59:59-04:00"
    assert cloud_row["spot"] == sample_db_row["spot"]


def test_write_snapshot_failure_returns_false_and_enqueues(
    mock_supabase_client,
    sample_db_row: dict,
    reset_supabase_writer,
    monkeypatch,
    tmp_path,
) -> None:
    # Force the .execute() call to raise — simulates a network/Supabase outage.
    mock_supabase_client.schema.return_value.table.return_value \
        .insert.return_value.execute.side_effect = RuntimeError("boom")

    # Redirect the dead-letter file into tmp_path so we don't pollute $HOME.
    fake_path = tmp_path / "supabase_pending_writes_gex.jsonl"
    monkeypatch.setattr("src.supabase_writer.PENDING_WRITES_PATH", fake_path)

    with patch("src.supabase_writer.create_client", return_value=mock_supabase_client):
        from src.supabase_writer import get_writer

        writer = get_writer()
        ok = writer.write_snapshot(local_id=7, row=sample_db_row)

    assert ok is False
    assert fake_path.exists(), "Failed write should be dead-lettered to JSONL"

    lines = fake_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["local_id"] == 7
    assert "boom" in entry["error"]


def test_write_snapshot_mapping_error_returns_false(
    mock_supabase_client,
    reset_supabase_writer,
    monkeypatch,
    tmp_path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A row that fails _to_cloud_row must be dead-lettered, not raised."""
    def boom(_local_id, _row):
        raise ValueError("mapping kaboom")

    monkeypatch.setattr(sw, "_to_cloud_row", boom)
    fake_path = tmp_path / "supabase_pending_writes_gex.jsonl"
    monkeypatch.setattr(sw, "PENDING_WRITES_PATH", fake_path)

    with patch("src.supabase_writer.create_client", return_value=mock_supabase_client):
        writer = sw.get_writer()
        with caplog.at_level(logging.WARNING, logger="src.supabase_writer"):
            ok = writer.write_snapshot(local_id=11, row={"timestamp": "x"})

    assert ok is False
    assert fake_path.exists()
    entry = json.loads(fake_path.read_text().strip().splitlines()[0])
    assert entry["local_id"] == 11
    assert "mapping_error" in entry["error"]

    # And we must NOT have called the client.
    mock_supabase_client.schema.assert_not_called()

    # And the mapping-error path must log at WARNING.
    warnings = [
        r for r in caplog.records
        if r.levelno == logging.WARNING
        and "[gex_writer] failed to map row" in r.getMessage()
    ]
    assert warnings, "Mapping error should log a WARNING record"


def test_write_snapshot_enqueue_io_error_is_logged_but_swallowed(
    mock_supabase_client,
    sample_db_row: dict,
    reset_supabase_writer,
    monkeypatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If the dead-letter file write itself raises, the writer must NOT
    crash. It logs an ERROR and continues. This exercises the outer
    try/except in `_enqueue`."""
    mock_supabase_client.schema.return_value.table.return_value \
        .insert.return_value.execute.side_effect = RuntimeError("network down")

    # Path.open inside _enqueue will raise.
    fake_path = (Path := __import__("pathlib").Path)("/nonexistent_root_xyz/locked/file.jsonl")
    monkeypatch.setattr(sw, "PENDING_WRITES_PATH", fake_path)

    with patch("src.supabase_writer.create_client", return_value=mock_supabase_client):
        writer = sw.get_writer()
        with caplog.at_level(logging.ERROR, logger="src.supabase_writer"):
            ok = writer.write_snapshot(local_id=99, row=sample_db_row)

    assert ok is False
    crit = [
        r for r in caplog.records
        if r.levelno == logging.ERROR
        and "[gex_writer] CRITICAL: failed to enqueue" in r.getMessage()
    ]
    assert crit, "Dead-letter write failure should log ERROR"


def test_get_writer_is_singleton() -> None:
    a = sw.get_writer()
    b = sw.get_writer()
    assert a is b, "get_writer must return the same singleton instance"


def test_write_snapshot_log_level_is_info(
    mock_supabase_client,
    sample_db_row: dict,
    reset_supabase_writer,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Re-affirm the regression-test assertion in a dedicated file.

    The full regression test lives in `tests/test_logging.py`. This is
    a sanity duplicate so a Supabase-specific failure surfaces in the
    right test file too.
    """
    with patch("src.supabase_writer.create_client", return_value=mock_supabase_client):
        from src.supabase_writer import get_writer

        writer = get_writer()
        with caplog.at_level(logging.DEBUG, logger="src.supabase_writer"):
            ok = writer.write_snapshot(local_id=123, row=sample_db_row)

    assert ok is True
    info_records = [
        r for r in caplog.records
        if r.levelno == logging.INFO
        and "[gex_writer] wrote snapshot local_id=123" in r.getMessage()
    ]
    assert info_records, (
        "Supabase success must log at INFO; got "
        f"{[(r.levelname, r.getMessage()) for r in caplog.records]}"
    )


# ---------------------------------------------------------------------------
# retry_pending_writes
# ---------------------------------------------------------------------------


def test_retry_pending_writes_with_no_file_returns_zero_zero(
    reset_supabase_writer,
    monkeypatch,
    tmp_path,
) -> None:
    fake_path = tmp_path / "nope.jsonl"
    monkeypatch.setattr(sw, "PENDING_WRITES_PATH", fake_path)

    writer = sw.get_writer()
    assert writer.retry_pending_writes() == (0, 0)


def test_retry_pending_writes_succeeds_and_removes_file(
    mock_supabase_client,
    reset_supabase_writer,
    monkeypatch,
    tmp_path,
) -> None:
    fake_path = tmp_path / "pending.jsonl"
    fake_path.write_text(
        json.dumps(
            {
                "ts": "2026-06-19T16:00:00+00:00",
                "local_id": 1,
                "row": {"timestamp": "2026-06-19 16:00:00"},
                "error": "x",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sw, "PENDING_WRITES_PATH", fake_path)

    with patch("src.supabase_writer.create_client", return_value=mock_supabase_client):
        writer = sw.get_writer()
        succeeded, failed = writer.retry_pending_writes()

    assert succeeded == 1
    assert failed == 0
    assert not fake_path.exists(), "Successful retry should clear the dead-letter file"


def test_retry_pending_writes_with_blank_and_garbage_lines(
    mock_supabase_client,
    reset_supabase_writer,
    monkeypatch,
    tmp_path,
) -> None:
    fake_path = tmp_path / "pending.jsonl"
    # Mix of: blank lines (skipped silently), garbage (JSONDecodeError → continue),
    # one valid line that will be retried.
    fake_path.write_text(
        "\n"
        "not json\n"
        + json.dumps(
            {
                "ts": "2026-06-19T16:00:00+00:00",
                "local_id": 1,
                "row": {"timestamp": "2026-06-19 16:00:00"},
                "error": "x",
            }
        )
        + "\n\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sw, "PENDING_WRITES_PATH", fake_path)

    with patch("src.supabase_writer.create_client", return_value=mock_supabase_client):
        writer = sw.get_writer()
        succeeded, failed = writer.retry_pending_writes()

    assert succeeded == 1
    assert failed == 0


def test_retry_pending_writes_records_failure(
    mock_supabase_client,
    reset_supabase_writer,
    monkeypatch,
    tmp_path,
) -> None:
    """If a retried entry still fails, count is incremented and file stays."""
    mock_supabase_client.schema.return_value.table.return_value \
        .insert.return_value.execute.side_effect = RuntimeError("still down")

    fake_path = tmp_path / "pending.jsonl"
    fake_path.write_text(
        json.dumps(
            {
                "ts": "2026-06-19T16:00:00+00:00",
                "local_id": 1,
                "row": {"timestamp": "2026-06-19 16:00:00"},
                "error": "x",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sw, "PENDING_WRITES_PATH", fake_path)

    with patch("src.supabase_writer.create_client", return_value=mock_supabase_client):
        writer = sw.get_writer()
        succeeded, failed = writer.retry_pending_writes()

    assert succeeded == 0
    assert failed == 1
    assert fake_path.exists(), "Failed retry must keep the dead-letter file"


# ---------------------------------------------------------------------------
# _create_client
# ---------------------------------------------------------------------------


def test_create_client_raises_when_supabase_missing(monkeypatch) -> None:
    monkeypatch.setattr(sw, "create_client", None)
    with pytest.raises(RuntimeError, match="supabase package is not installed"):
        sw._create_client()


def test_create_client_raises_when_creds_missing(monkeypatch) -> None:
    # Production calls load_dotenv(override=False) which would refill from .env;
    # stub it out so the runtime-only "creds missing" branch is exercised.
    monkeypatch.setattr("src.supabase_writer.load_dotenv", lambda *a, **kw: None)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError, match="SUPABASE_URL"):
        sw._create_client()


def test_create_client_uses_env(monkeypatch) -> None:
    monkeypatch.setattr("src.supabase_writer.load_dotenv", lambda *a, **kw: None)
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "fake-key")
    sentinel = object()
    monkeypatch.setattr(sw, "create_client", lambda url, key: (url, key, sentinel))
    result = sw._create_client()
    assert result[2] is sentinel
    assert result[0] == "https://example.supabase.co"
    assert result[1] == "fake-key"


# ---------------------------------------------------------------------------
# Import-time fallback when supabase is not installed
# ---------------------------------------------------------------------------


def test_module_import_handles_missing_supabase(monkeypatch) -> None:
    """If `from supabase import Client, create_client` raises ImportError,
    the module sets Client/create_client to None so subsequent code can
    detect the missing package. We exercise this by re-running the import
    with a stubbed-out supabase module.
    """
    import importlib
    import sys

    # Drop the cached module + create_client sentinel so the ImportError
    # branch fires on re-import.
    monkeypatch.delitem(sys.modules, "supabase", raising=False)
    monkeypatch.setitem(sys.modules, "supabase", None)  # forces ImportError

    reloaded = importlib.reload(sw)
    assert reloaded.Client is None
    assert reloaded.create_client is None
