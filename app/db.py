"""SQLite storage. The only module that knows the schema and runs queries."""

import sqlite3
from contextlib import closing
from datetime import datetime, timezone

from cryptography.fernet import Fernet

from app import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    webhook_id  TEXT PRIMARY KEY,
    type        TEXT NOT NULL,
    received_at TEXT NOT NULL,
    handled     INTEGER NOT NULL DEFAULT 0
);

-- One row per sub-account that installed the OAuth app. Tokens are Fernet-encrypted.
-- calendar_id, pipeline_id and booked_stage_id stay NULL until Step 4 fills them.
CREATE TABLE IF NOT EXISTS installs (
    location_id     TEXT PRIMARY KEY,
    access_token    TEXT NOT NULL,
    refresh_token   TEXT NOT NULL,
    expires_at      TEXT NOT NULL,
    calendar_id     TEXT,
    pipeline_id     TEXT,
    booked_stage_id TEXT
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


def _fernet() -> Fernet:
    if not config.APP_ENCRYPTION_KEY:
        raise RuntimeError("APP_ENCRYPTION_KEY is not set: see README Step 5 to generate one")
    return Fernet(config.APP_ENCRYPTION_KEY)


def save_install(location_id: str, access_token: str, refresh_token: str, expires_at: datetime) -> None:
    """Insert or update the tokens of a location; keeps its calendar and pipeline ids."""
    f = _fernet()
    with closing(_connect()) as conn, conn:
        conn.execute(
            """
            INSERT INTO installs (location_id, access_token, refresh_token, expires_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (location_id) DO UPDATE SET
                access_token = excluded.access_token,
                refresh_token = excluded.refresh_token,
                expires_at = excluded.expires_at
            """,
            (
                location_id,
                f.encrypt(access_token.encode()).decode(),
                f.encrypt(refresh_token.encode()).decode(),
                expires_at.isoformat(),
            ),
        )


def get_install(location_id: str) -> dict | None:
    """The install of a location with tokens decrypted and `expires_at` as a datetime."""
    with closing(_connect()) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM installs WHERE location_id = ?", (location_id,)).fetchone()
    if row is None:
        return None
    f = _fernet()
    install = dict(row)
    install["access_token"] = f.decrypt(row["access_token"].encode()).decode()
    install["refresh_token"] = f.decrypt(row["refresh_token"].encode()).decode()
    install["expires_at"] = datetime.fromisoformat(row["expires_at"])
    return install
