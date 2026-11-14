"""SQLite storage. The only module that knows the schema and runs queries."""

import sqlite3
from contextlib import closing
from datetime import datetime, timezone

from app import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    webhook_id  TEXT PRIMARY KEY,
    type        TEXT NOT NULL,
    received_at TEXT NOT NULL,
    handled     INTEGER NOT NULL DEFAULT 0
);
"""


def _connect() -> sqlite3.Connection:
    return sqlite3.connect(config.DATABASE_PATH)


def init() -> None:
    """Create missing tables. Safe to run on every start."""
    with closing(_connect()) as conn, conn:
        conn.executescript(SCHEMA)


def insert_event(webhook_id: str, event_type: str) -> bool:
    """Record a webhook. False if this webhook id was already recorded (a GHL resend)."""
    with closing(_connect()) as conn, conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO events (webhook_id, type, received_at) VALUES (?, ?, ?)",
            (webhook_id, event_type, datetime.now(timezone.utc).isoformat()),
        )
        return cur.rowcount == 1


def mark_handled(webhook_id: str) -> None:
    with closing(_connect()) as conn, conn:
        conn.execute("UPDATE events SET handled = 1 WHERE webhook_id = ?", (webhook_id,))


def get_event(webhook_id: str) -> dict | None:
    with closing(_connect()) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM events WHERE webhook_id = ?", (webhook_id,)).fetchone()
        return dict(row) if row else None
