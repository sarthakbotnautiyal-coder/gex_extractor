"""Regression test for the Supabase success-log visibility bug.

On 2026-06-19 we discovered that `src.supabase_writer.write_snapshot()`
logged every successful cloud write at `logging.DEBUG`. Since the rest
of the project (and `log_setup.get_logger`) defaults to `INFO`, that
meant every successful Supabase write was invisible in production logs.

This test asserts the post-fix behavior:
- A successful `write_snapshot(...)` emits an `INFO`-level record whose
  message contains `[gex_writer] wrote snapshot local_id=<N>`.
- It does NOT emit a `DEBUG`-level record with the same substring.

If anyone "reverts" the log level back to DEBUG, this test must fail.
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch

import pytest


def _expected_substring(local_id: int) -> str:
    return f"[gex_writer] wrote snapshot local_id={local_id}"


def test_supabase_success_log_is_info_not_debug(
    mock_supabase_client,
    sample_db_row: dict,
    reset_supabase_writer,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """SupabaseGexWriter.write_snapshot success must log at INFO."""
    # Patch src.supabase_writer.create_client so no real Supabase hit occurs.
    with patch("src.supabase_writer.create_client", return_value=mock_supabase_client):
        # Import inside the patch context so the module-level reference resolves
        # through create_client, not through a cached import.
        from src.supabase_writer import get_writer

        writer = get_writer()

        with caplog.at_level(logging.DEBUG, logger="src.supabase_writer"):
            ok = writer.write_snapshot(local_id=999, row=sample_db_row)

    assert ok is True, "write_snapshot should return True on mocked success"

    info_hits = [
        r
        for r in caplog.records
        if r.levelno == logging.INFO
        and _expected_substring(999) in r.getMessage()
    ]
    debug_hits = [
        r
        for r in caplog.records
        if r.levelno == logging.DEBUG
        and _expected_substring(999) in r.getMessage()
    ]

    assert info_hits, (
        "Expected an INFO log record containing "
        f"{_expected_substring(999)!r}, got levels: "
        f"{[(r.levelname, r.getMessage()) for r in caplog.records]}"
    )
    assert not debug_hits, (
        "Did NOT expect a DEBUG log record containing "
        f"{_expected_substring(999)!r}; the fix moved this to INFO. "
        f"Got: {[(r.levelname, r.getMessage()) for r in caplog.records]}"
    )


def test_log_setup_creates_date_prefixed_file(tmp_logs_dir) -> None:
    """get_logger(name) must create logs/<name>.YYYY-MM-DD.log."""
    from src.log_setup import _utc_date_str, get_logger

    name = "gex_extractor_smoke"
    log = get_logger(name, log_dir=str(tmp_logs_dir))

    expected = tmp_logs_dir / f"{name}.{_utc_date_str()}.log"
    assert expected.exists(), f"Expected log file {expected} to exist"

    # Handlers on a freshly-fetched logger: exactly one FileHandler.
    file_handlers = [h for h in log.handlers if isinstance(h, logging.FileHandler)]
    assert len(file_handlers) == 1
    assert Path(file_handlers[0].baseFilename).resolve() == expected.resolve()


def test_log_setup_no_duplicate_handlers(tmp_logs_dir) -> None:
    """Calling get_logger(name) a second time must NOT add a duplicate FileHandler."""
    from src.log_setup import get_logger

    name = "gex_extractor_dup"
    log1 = get_logger(name, log_dir=str(tmp_logs_dir))
    log2 = get_logger(name, log_dir=str(tmp_logs_dir))

    # Same logger object — getLogger returns the singleton.
    assert log1 is log2

    file_handlers = [h for h in log2.handlers if isinstance(h, logging.FileHandler)]
    assert len(file_handlers) == 1, (
        f"Expected exactly 1 FileHandler, got {len(file_handlers)}"
    )


def test_log_setup_handler_swaps_on_date_rollover(
    tmp_logs_dir, monkeypatch
) -> None:
    """If UTC date rolls over, the next get_logger call must swap the file."""
    import src.log_setup as log_setup_mod
    from src.log_setup import get_logger

    name = "gex_extractor_rollover"

    log = get_logger(name, log_dir=str(tmp_logs_dir))
    file_handlers_initial = [
        h for h in log.handlers if isinstance(h, logging.FileHandler)
    ]
    assert len(file_handlers_initial) == 1
    initial_path = Path(file_handlers_initial[0].baseFilename).resolve()

    # Pretend we crossed UTC midnight into "tomorrow".
    tomorrow = "2099-01-01"
    monkeypatch.setattr(log_setup_mod, "_utc_date_str", lambda: tomorrow)

    log2 = get_logger(name, log_dir=str(tmp_logs_dir))
    assert log2 is log

    file_handlers_after = [
        h for h in log.handlers if isinstance(h, logging.FileHandler)
    ]
    assert len(file_handlers_after) == 1, (
        "Handler swap should leave exactly 1 FileHandler attached"
    )
    new_path = Path(file_handlers_after[0].baseFilename).resolve()

    assert new_path != initial_path, (
        f"Expected handler to swap to new day's file, "
        f"but path unchanged: {initial_path}"
    )
    assert new_path.name == f"{name}.{tomorrow}.log"


def test_log_setup_handles_unresolvable_baseFilename(tmp_logs_dir, monkeypatch) -> None:
    """If Path(existing.baseFilename).resolve() raises, the fallback path runs.

    The `_ensure_current_day_handler` helper guards Path.resolve() with a
    try/except so a hostile environment (broken cwd, etc.) can't take the
    logger down. We force the exception by patching Path.resolve to raise.
    """
    import src.log_setup as log_setup_mod
    from src.log_setup import _ensure_current_day_handler, get_logger

    name = "gex_extractor_resolve_fail"

    # First call installs the handler normally.
    log = get_logger(name, log_dir=str(tmp_logs_dir))
    file_handlers = [h for h in log.handlers if isinstance(h, logging.FileHandler)]
    assert len(file_handlers) == 1

    # Now make Path.resolve raise whenever called on this baseFilename.
    orig_resolve = Path.resolve

    def boom(self, *args, **kwargs):  # noqa: ANN001, ANN201
        if str(self) == file_handlers[0].baseFilename:
            raise OSError("simulated unresolvable path")
        return orig_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", boom)
    monkeypatch.setattr(
        log_setup_mod, "_utc_date_str", lambda: "2099-12-31"
    )

    # Should NOT raise — the except-branch reinstalls a fresh handler.
    _ensure_current_day_handler(log, Path(str(tmp_logs_dir)), name)

    file_handlers_after = [
        h for h in log.handlers if isinstance(h, logging.FileHandler)
    ]
    assert len(file_handlers_after) == 1
    assert file_handlers_after[0] is not file_handlers[0], (
        "Old handler should have been replaced"
    )


def test_log_setup_current_file_handler_returns_none_for_stream_only(
    tmp_logs_dir,
) -> None:
    """The `_current_file_handler` helper returns None when only a StreamHandler is present."""
    import logging as _logging

    from src.log_setup import _current_file_handler

    bare = _logging.getLogger("test_no_file_handler")
    # Ensure the logger has at least one handler that isn't a FileHandler.
    bare.addHandler(_logging.StreamHandler())
    assert _current_file_handler(bare) is None
