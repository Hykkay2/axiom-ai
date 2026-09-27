"""Business rules for referrals and kickbacks."""

import sqlite3
from dataclasses import dataclass

from .db import Database, new_referral_code


@dataclass(frozen=True)
class RewardRules:
    # Paid to both the referrer and the new member when a referral signs up.
    signup_bonus_cents: int = 500
    # Share of each purchase by a referred member paid back to their referrer.
    kickback_rate: float = 0.05
    # Minimum balance before a payout can be requested.
    min_payout_cents: int = 1000


class KickbacksError(Exception):
    """A request that breaks a business rule (maps to HTTP 400/404)."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class KickbacksService:
    def __init__(self, db: Database, rules: RewardRules | None = None):
        self.db = db
        self.rules = rules or RewardRules()

    # --- members -----------------------------------------------------------

    def create_member(self, name: str, email: str, referral_code: str | None = None) -> dict:
        name, email = name.strip(), email.strip().lower()
        if not name or "@" not in email:
            raise KickbacksError("A name and a valid email are required.")

        with self.db.connect() as conn:
            referrer = None
            if referral_code:
                referrer = conn.execute(
                    "SELECT * FROM members WHERE referral_code = ?",
                    (referral_code.strip().upper(),),
                ).fetchone()
                if referrer is None:
                    raise KickbacksError("Unknown referral code.", 404)

            code = self._unique_code(conn)
            try:
                cur = conn.execute(
                    "INSERT INTO members (name, email, referral_code, referred_by) VALUES (?, ?, ?, ?)",
                    (name, email, code, referrer["id"] if referrer else None),
                )
            except sqlite3.IntegrityError:
                raise KickbacksError("A member with that email already exists.")
            member_id = cur.lastrowid

            if referrer is not None and self.rules.signup_bonus_cents:
                bonus = self.rules.signup_bonus_cents
                conn.executemany(
                    "INSERT INTO ledger (member_id, amount_cents, kind, source_id, note) VALUES (?, ?, 'signup_bonus', ?, ?)",
                    [
                        (referrer["id"], bonus, member_id, f"{name} joined with your code"),
                        (member_id, bonus, referrer["id"], f"Welcome bonus via {referrer['name']}"),
                    ],
                )

        return self.get_member(member_id)

    def get_member(self, member_id: int) -> dict:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM members WHERE id = ?", (member_id,)).fetchone()
            if row is None:
                raise KickbacksError("Member not found.", 404)
            return self._member_dict(conn, row)

    def list_members(self) -> list[dict]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM members ORDER BY id").fetchall()
            return [self._member_dict(conn, r) for r in rows]

    # --- money -------------------------------------------------------------

    def record_purchase(self, member_id: int, amount_cents: int) -> dict:
        if amount_cents <= 0:
            raise KickbacksError("Purchase amount must be positive.")

        with self.db.connect() as conn:
            member = conn.execute("SELECT * FROM members WHERE id = ?", (member_id,)).fetchone()
            if member is None:
                raise KickbacksError("Member not found.", 404)

            cur = conn.execute(
                "INSERT INTO purchases (member_id, amount_cents) VALUES (?, ?)",
                (member_id, amount_cents),
            )
            purchase_id = cur.lastrowid

            kickback = 0
            if member["referred_by"] is not None:
                kickback = round(amount_cents * self.rules.kickback_rate)
                if kickback > 0:
                    conn.execute(
                        "INSERT INTO ledger (member_id, amount_cents, kind, source_id, note) VALUES (?, ?, 'kickback', ?, ?)",
                        (
                            member["referred_by"],
                            kickback,
                            purchase_id,
                            f"{self.rules.kickback_rate:.0%} of {member['name']}'s ${amount_cents / 100:.2f} purchase",
                        ),
                    )

        return {
            "purchase_id": purchase_id,
            "member_id": member_id,
            "amount_cents": amount_cents,
            "kickback_cents": kickback,
            "kickback_to": member["referred_by"],
        }

    def request_payout(self, member_id: int) -> dict:
        with self.db.connect() as conn:
            if conn.execute("SELECT 1 FROM members WHERE id = ?", (member_id,)).fetchone() is None:
                raise KickbacksError("Member not found.", 404)
            balance = self._balance(conn, member_id)
            if balance < self.rules.min_payout_cents:
                raise KickbacksError(
                    f"Balance ${balance / 100:.2f} is below the ${self.rules.min_payout_cents / 100:.2f} payout minimum."
                )
            conn.execute(
                "INSERT INTO ledger (member_id, amount_cents, kind, note) VALUES (?, ?, 'payout', 'Payout requested')",
                (member_id, -balance),
            )
        return {"member_id": member_id, "paid_out_cents": balance}

    def ledger(self, member_id: int) -> list[dict]:
        self.get_member(member_id)
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM ledger WHERE member_id = ? ORDER BY id DESC", (member_id,)
            ).fetchall()
            return [dict(r) for r in rows]

    def leaderboard(self, limit: int = 10) -> list[dict]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT m.id, m.name,
                       (SELECT COUNT(*) FROM members r WHERE r.referred_by = m.id) AS referrals,
                       COALESCE((SELECT SUM(amount_cents) FROM ledger l
                                 WHERE l.member_id = m.id AND l.amount_cents > 0), 0) AS lifetime_earned_cents
                FROM members m
                ORDER BY lifetime_earned_cents DESC, referrals DESC, m.id
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    # --- helpers -----------------------------------------------------------

    def _unique_code(self, conn) -> str:
        while True:
            code = new_referral_code()
            if conn.execute("SELECT 1 FROM members WHERE referral_code = ?", (code,)).fetchone() is None:
                return code

    @staticmethod
    def _balance(conn, member_id: int) -> int:
        return conn.execute(
            "SELECT COALESCE(SUM(amount_cents), 0) FROM ledger WHERE member_id = ?", (member_id,)
        ).fetchone()[0]

    def _member_dict(self, conn, row) -> dict:
        member = dict(row)
        member["balance_cents"] = self._balance(conn, row["id"])
        member["lifetime_earned_cents"] = conn.execute(
            "SELECT COALESCE(SUM(amount_cents), 0) FROM ledger WHERE member_id = ? AND amount_cents > 0",
            (row["id"],),
        ).fetchone()[0]
        member["referral_count"] = conn.execute(
            "SELECT COUNT(*) FROM members WHERE referred_by = ?", (row["id"],)
        ).fetchone()[0]
        return member
