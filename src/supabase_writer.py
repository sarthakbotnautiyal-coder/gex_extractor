"""Supabase dual-write writer for GEX snapshots."""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv

try:
    from supabase import Client, create_client
except ImportError:
    Client = None
    create_client = None

logger = logging.getLogger(__name__)

CLOUD_SCHEMA = "trading"
CLOUD_TABLE = "gex_snapshots"
PENDING_WRITES_PATH = Path.home() / "supabase_pending_writes_gex.jsonl"
GEX_SOURCE = "gex_bot"

_init_lock = threading.Lock()
_writer_instance: "SupabaseGexWriter | None" = None


def _normalize_timestamp(value: str | None) -> str | None:
    """Normalize a local timestamp to ISO 8601 with offset."""
    if value is None:
        return None
    s = value.strip()
    if not s:
        return None
    if "T" in s and ("Z" in s or "+" in s[10:] or s.count("-") > 2):
        return s
    try:
        dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except ValueError:
        return s


def _to_cloud_row(local_id: int, row: dict[str, Any]) -> dict[str, Any]:
    """Map a local SQLite row to a cloud-ready dict."""
    cloud_row: dict[str, Any] = {
        "raw_id_local": int(local_id),
        "source": GEX_SOURCE,
        "snapshot_timestamp": _normalize_timestamp(row.get("timestamp")),
        "received_at": _normalize_timestamp(row.get("received_at")),
        "gex_by_oi": row.get("gex_by_oi"),
        "gex_by_volume": row.get("gex_by_volume"),
        "spot": row.get("spot"),
        "major_negative_by_volume": row.get("major_negative_by_volume"),
        "major_positive_by_volume": row.get("major_positive_by_volume"),
        "major_negative_by_oi": row.get("major_negative_by_oi"),
        "major_positive_by_oi": row.get("major_positive_by_oi"),
        "zero_gamma": row.get("zero_gamma"),
        "raw_message": row.get("raw_message"),
    }
    return cloud_row


class SupabaseGexWriter:
    """Best-effort dual-write writer for GEX snapshots."""

    def __init__(self) -> None:
        self._client: Optional[Client] = None

    def _get_client(self) -> Client:
        """Create the Supabase client on first use (thread-safe)."""
        if self._client is None:
            with _init_lock:
                if self._client is None:
                    self._client = _create_client()
        return self._client

    def write_snapshot(self, local_id: int, row: dict[str, Any]) -> bool:
        """Dual-write a GEX snapshot row to Supabase."""
        try:
            cloud_row = _to_cloud_row(local_id, row)
        except Exception as e:
            logger.warning("[gex_writer] failed to map row (local_id=%s): %s", local_id, e)
            self._enqueue(local_id, row, error=f"mapping_error: {e}")
            return False

        try:
            client = self._get_client()
            client.schema(CLOUD_SCHEMA).table(CLOUD_TABLE).insert(cloud_row).execute()
            logger.info("[gex_writer] wrote snapshot local_id=%s ts=%s",
                        local_id, cloud_row.get("snapshot_timestamp"))
            return True
        except Exception as e:
            logger.warning("[gex_writer] cloud write failed (local_id=%s): %s", local_id, e)
            self._enqueue(local_id, row, error=str(e))
            return False

    def _enqueue(self, local_id: int, row: dict[str, Any], error: str) -> None:
        """Append a failed write to the JSONL retry file."""
        try:
            PENDING_WRITES_PATH.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "local_id": int(local_id),
                "row": dict(row),
                "error": error,
            }
            with PENDING_WRITES_PATH.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except Exception as e:
            logger.error("[gex_writer] CRITICAL: failed to enqueue dead-letter (local_id=%s): %s", local_id, e)

    def retry_pending_writes(self) -> tuple[int, int]:
        """Retry any writes that previously failed."""
        if not PENDING_WRITES_PATH.exists():
            return (0, 0)
        succeeded = 0
        failed = 0
        entries: list[dict[str, Any]] = []
        with PENDING_WRITES_PATH.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        for entry in entries:
            local_id = entry.get("local_id")
            row = entry.get("row", {})
            if self.write_snapshot(local_id=local_id, row=row):
                succeeded += 1
            else:
                failed += 1
        if failed == 0:
            try:
                PENDING_WRITES_PATH.unlink()
            except FileNotFoundError:
                pass
        return (succeeded, failed)


def get_writer() -> SupabaseGexWriter:
    """Return the module-level singleton writer (thread-safe init)."""
    global _writer_instance
    if _writer_instance is None:
        with _init_lock:
            if _writer_instance is None:
                _writer_instance = SupabaseGexWriter()
    return _writer_instance


def _create_client() -> Client:
    """Create the Supabase client."""
    if create_client is None:
        raise RuntimeError("supabase package is not installed")
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=False)
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SECRET_KEY")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SECRET_KEY must be set")
    return create_client(url, key)
