"""Pending proposals survive restarts (SQLite). Confirmed events are remembered so they can be undone."""
from __future__ import annotations

import json
import sqlite3

from .models import Proposal


class Store:
    def __init__(self, path: str):
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS proposals (id TEXT PRIMARY KEY, user_id TEXT, body TEXT, created REAL DEFAULT (strftime('%s','now')));
            CREATE TABLE IF NOT EXISTS messages (chat_id TEXT, message_id TEXT, proposal_id TEXT, PRIMARY KEY (chat_id, message_id));
            CREATE TABLE IF NOT EXISTS created (proposal_id TEXT, user_id TEXT, event_id TEXT);
            """
        )
        try:  # migrate a pre-users `created` table in an existing db
            self._db.execute("ALTER TABLE created ADD COLUMN user_id TEXT")
        except sqlite3.OperationalError:
            pass

    def save(self, p: Proposal) -> None:
        with self._db:
            self._db.execute("INSERT OR REPLACE INTO proposals (id, user_id, body) VALUES (?,?,?)", (p.id, p.user_id, p.model_dump_json()))

    def get(self, proposal_id: str) -> Proposal | None:
        row = self._db.execute("SELECT body FROM proposals WHERE id=?", (proposal_id,)).fetchone()
        return Proposal.model_validate(json.loads(row[0])) if row else None

    def take(self, proposal_id: str) -> Proposal | None:
        """Fetch and remove atomically, so a double-tapped Confirm can't create events twice."""
        with self._db:
            row = self._db.execute("SELECT body FROM proposals WHERE id=?", (proposal_id,)).fetchone()
            if not row:
                return None
            self._db.execute("DELETE FROM proposals WHERE id=?", (proposal_id,))
        return Proposal.model_validate(json.loads(row[0]))

    def record_created(self, proposal_id: str, user_id: str, event_ids: list[str]) -> None:
        with self._db:
            self._db.executemany("INSERT INTO created VALUES (?,?,?)", [(proposal_id, user_id, e) for e in event_ids])

    def pop_created(self, proposal_id: str, user_id: str | None = None) -> list[str]:
        """Remove and return the created event ids. With `user_id`, only that user's rows."""
        with self._db:
            if user_id is None:
                where, args = "proposal_id=?", (proposal_id,)
            else:
                where, args = "proposal_id=? AND user_id=?", (proposal_id, user_id)
            ids = [r[0] for r in self._db.execute(f"SELECT event_id FROM created WHERE {where}", args)]
            self._db.execute(f"DELETE FROM created WHERE {where}", args)
        return ids

    def link_message(self, chat_id: str, message_id: str, proposal_id: str) -> None:
        with self._db:
            self._db.execute("INSERT OR REPLACE INTO messages VALUES (?,?,?)", (chat_id, message_id, proposal_id))

    def proposal_for_message(self, chat_id: str, message_id: str) -> str | None:
        row = self._db.execute("SELECT proposal_id FROM messages WHERE chat_id=? AND message_id=?", (chat_id, message_id)).fetchone()
        return row[0] if row else None
