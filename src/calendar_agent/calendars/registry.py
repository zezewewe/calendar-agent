"""Which Google calendar holds each user's events.

One Google account (the agent's OAuth token) owns a secondary calendar per
user. This maps a verified email to that calendar id so the same user keeps the
same calendar across sessions, and so no two users share events.
"""
from __future__ import annotations

import sqlite3


class CalendarRegistry:
    def __init__(self, path: str):
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.executescript(
            "CREATE TABLE IF NOT EXISTS user_calendars ("
            "email TEXT PRIMARY KEY, calendar_id TEXT NOT NULL, "
            "created REAL DEFAULT (strftime('%s','now')));"
        )

    def get(self, email: str) -> str | None:
        row = self._db.execute(
            "SELECT calendar_id FROM user_calendars WHERE email=?", (email.strip().lower(),)
        ).fetchone()
        return row[0] if row else None

    def set(self, email: str, calendar_id: str) -> None:
        with self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO user_calendars (email, calendar_id) VALUES (?,?)",
                (email.strip().lower(), calendar_id),
            )
