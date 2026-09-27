"""SQLite storage for members, referrals and the rewards ledger."""

import secrets
import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS members (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    email         TEXT NOT NULL UNIQUE,
    referral_code TEXT NOT NULL UNIQUE,
    referred_by   INTEGER REFERENCES members(id),
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS purchases (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id    INTEGER NOT NULL REFERENCES members(id),
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS ledger (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id    INTEGER NOT NULL REFERENCES members(id),
    amount_cents INTEGER NOT NULL,
    kind         TEXT NOT NULL CHECK (kind IN ('signup_bonus', 'kickback', 'payout')),
    source_id    INTEGER,
    note         TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def new_referral_code() -> str:
    # 8 chars, no ambiguous characters (0/O, 1/I/L).
    alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(8))


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
