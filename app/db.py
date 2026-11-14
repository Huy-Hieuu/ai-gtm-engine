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
-- NULL calendar_id, pipeline_id or booked_stage_id: Step 4 uses the GHL_* env value instead.
CREATE TABLE IF NOT EXISTS installs (
    location_id     TEXT PRIMARY KEY,
    access_token    TEXT NOT NULL,
    refresh_token   TEXT NOT NULL,
    expires_at      TEXT NOT NULL,
    calendar_id     TEXT,
    pipeline_id     TEXT,
    booked_stage_id TEXT
);

-- One row per contact the agent talks to. Times are UTC ISO strings.
CREATE TABLE IF NOT EXISTS leads (
    contact_id      TEXT PRIMARY KEY,
    first_name      TEXT NOT NULL DEFAULT '',
    state           TEXT NOT NULL,
    need            TEXT,
    budget          TEXT,
    timing          TEXT,
    created_at      TEXT NOT NULL,
    last_msg_at     TEXT,              -- last question we sent; nudges count from here
    nudges          INTEGER NOT NULL DEFAULT 0,
    last_inbound_id TEXT,              -- GHL messageId of the last reply handled
    offered_slots   TEXT,              -- JSON list of the slot start times last offered (Step 4)
    offers          INTEGER NOT NULL DEFAULT 0  -- times slots were offered
);

CREATE TABLE IF NOT EXISTS metrics (
    contact_id          TEXT PRIMARY KEY,
    first_reply_seconds REAL,          -- contact created -> first SMS sent
    qualified           INTEGER NOT NULL DEFAULT 0,
    booked              INTEGER NOT NULL DEFAULT 0
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


LEAD_FIELDS = {
    "first_name", "state", "need", "budget", "timing", "last_msg_at", "nudges", "offered_slots", "offers",
}
METRIC_FIELDS = {"first_reply_seconds", "qualified", "booked"}


def insert_lead(contact_id: str, first_name: str, state: str, created_at: str) -> bool:
    """Start tracking a contact. False if it is already tracked."""
    with closing(_connect()) as conn, conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO leads (contact_id, first_name, state, created_at) "
            "VALUES (?, ?, ?, ?)",
            (contact_id, first_name, state, created_at),
        )
        return cur.rowcount == 1


def get_lead(contact_id: str) -> dict | None:
    with closing(_connect()) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM leads WHERE contact_id = ?", (contact_id,)).fetchone()
        return dict(row) if row else None


def update_lead(contact_id: str, **fields) -> None:
    unknown = fields.keys() - LEAD_FIELDS
    if unknown:
        raise ValueError(f"not lead fields: {sorted(unknown)}")
    sets = ", ".join(f"{name} = ?" for name in fields)
    with closing(_connect()) as conn, conn:
        conn.execute(f"UPDATE leads SET {sets} WHERE contact_id = ?", (*fields.values(), contact_id))


def claim_inbound(contact_id: str, message_id: str) -> bool:
    """Mark a reply as handled. False if it already was (same GHL messageId)."""
    with closing(_connect()) as conn, conn:
        cur = conn.execute(
            "UPDATE leads SET last_inbound_id = ? "
            "WHERE contact_id = ? AND (last_inbound_id IS NULL OR last_inbound_id != ?)",
            (message_id, contact_id, message_id),
        )
        return cur.rowcount == 1


def leads_to_nudge(states: tuple[str, ...], max_nudges: int) -> list[dict]:
    """Leads waiting on a reply that have not had every nudge yet."""
    marks = ", ".join("?" for _ in states)
    with closing(_connect()) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT * FROM leads WHERE state IN ({marks}) AND nudges < ? "
            "AND last_msg_at IS NOT NULL",
            (*states, max_nudges),
        ).fetchall()
        return [dict(r) for r in rows]


def claim_nudge(contact_id: str, nudges: int) -> bool:
    """Count nudge number `nudges + 1` as sent. False if another run already counted it."""
    with closing(_connect()) as conn, conn:
        cur = conn.execute(
            "UPDATE leads SET nudges = nudges + 1 WHERE contact_id = ? AND nudges = ?",
            (contact_id, nudges),
        )
        return cur.rowcount == 1


def set_metrics(contact_id: str, **fields) -> None:
    unknown = fields.keys() - METRIC_FIELDS
    if unknown:
        raise ValueError(f"not metric fields: {sorted(unknown)}")
    cols = ", ".join(fields)
    marks = ", ".join("?" for _ in fields)
    updates = ", ".join(f"{name} = excluded.{name}" for name in fields)
    with closing(_connect()) as conn, conn:
        conn.execute(
            f"INSERT INTO metrics (contact_id, {cols}) VALUES (?, {marks}) "
            f"ON CONFLICT(contact_id) DO UPDATE SET {updates}",
            (contact_id, *fields.values()),
        )


def get_metrics(contact_id: str) -> dict | None:
    with closing(_connect()) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM metrics WHERE contact_id = ?", (contact_id,)).fetchone()
        return dict(row) if row else None
